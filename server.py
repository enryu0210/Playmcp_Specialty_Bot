import os
import uvicorn
import asyncio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from mcp.server import Server
import mcp.types as types
from anyio import create_memory_object_stream
from coffee_tools import get_coffee_recommendations, get_criteria_info

# --- [1. 설정 및 앱 초기화] ---
app = FastAPI()
mcp_server = Server("Coffee-Recommender")

# [보안 설정]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

global_writer = None

# --- [2. 도구(Tool) 정의] ---
@mcp_server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="show_criteria",
            description="커피 추천 기준과 로직(산미, 고소함 등)을 보여줍니다.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="recommend_coffee",
            description="사용자의 취향(예: 산미, 고소함, 과일향 등)을 입력받아 알맞은 커피를 추천합니다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "preference": {
                        "type": "string",
                        "description": "사용자의 커피 취향 (예: '산미 있는거', '고소한 맛')",
                    }
                },
                "required": ["preference"],
            },
        ),
    ]

# --- [3. 도구 실행 로직 (안전장치 강화)] ---
@mcp_server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent]:
    
    # [안전장치] 모든 로직을 try-except로 감싸서 서버 다운 방지
    try:
        if name == "show_criteria":
            result = get_criteria_info()
            return [types.TextContent(type="text", text=result)]

        elif name == "recommend_coffee":
            preference = arguments.get("preference", "")
            
            # [수정] asyncio.to_thread 사용 (더 안전한 비동기 처리)
            # 타임아웃 15초 적용
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(get_coffee_recommendations, preference),
                    timeout=15.0
                )
            except asyncio.TimeoutError:
                return [types.TextContent(type="text", text="Error: 처리 시간이 너무 오래 걸립니다.")]
            except Exception as e:
                # 내부 로직 에러 캐치
                print(f"Logic Error: {e}")
                return [types.TextContent(type="text", text=f"Error inside logic: {str(e)}")]

            # 결과 처리 로직
            final_text = ""
            if isinstance(result, str):
                final_text = result
            elif isinstance(result, dict):
                if result.get("type") == "recommendation":
                    output = [f"### ☕ '{preference}' 취향 추천 결과"]
                    output.append(f"_{result.get('flavor_desc', '')}_")
                    
                    # 데이터 파싱 중 에러 방지
                    countries = result.get('countries', [])
                    for country in countries:
                        c_name = country.get('country_name', 'Unknown')
                        output.append(f"\n**[{c_name}]**")
                        for coffee in country.get('coffees', []):
                            c_name_item = coffee.get('name', 'Unknown')
                            c_rating = coffee.get('rating', '0')
                            c_desc = coffee.get('desc', '')[:100]
                            output.append(f"- {c_name_item} ({c_rating}점)")
                            output.append(f"  특징: {c_desc}...")
                    final_text = "\n".join(output)
                else:
                    final_text = result.get("content", "내용 없음")
            else:
                final_text = str(result)
            
            return [types.TextContent(type="text", text=final_text)]

        raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        # [최후의 방어선] 여기서 잡힌 에러가 "error while calling tool" 대신 출력됩니다.
        print(f"🔥 Critical Tool Error: {e}")
        return [types.TextContent(type="text", text=f"System Error: {str(e)}")]

# --- [4. 수동 배관 작업 (Wiring)] ---

@app.get("/")
async def handle_root():
    return {"status": "ok", "message": "Coffee MCP Server is Running!"}

@app.get("/sse")
async def handle_sse(request: Request):
    global global_writer
    client_write, client_read = create_memory_object_stream(10)
    server_write, server_read = create_memory_object_stream(10)
    global_writer = client_write

    async def run_mcp_server():
        try:
            await mcp_server.run(
                client_read, 
                server_write, 
                mcp_server.create_initialization_options()
            )
        except Exception as e:
            print(f"Server Error: {e}")

    asyncio.create_task(run_mcp_server())

    async def event_generator():
        yield {"event": "endpoint", "data": "/sse"}
        async with mcp_server.create_initialization_message() as init_msg:
            yield init_msg.model_dump()
        async with server_read:
            async for message in server_read:
                if hasattr(message, 'model_dump'):
                    yield message.model_dump()
                else:
                    yield message

    return EventSourceResponse(event_generator())

async def forward_post_to_server(request: Request):
    global global_writer
    if global_writer is None:
        try:
            data = await request.json()
            method = data.get("method")
            msg_id = data.get("id")
            
            if method == "initialize":
                print("👋 [Check] PlayMCP Initialize Handshake.")
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}, "prompts": {}, "resources": {}},
                        "serverInfo": {"name": "Coffee-Recommender", "version": "1.0"}
                    }
                }
            if method == "ping":
                return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
            
            if method == "tools/list":
                print("🛠️ [Check] PlayMCP asking for Tools List.")
                # 도구 목록 반환
                tools_list = await handle_list_tools()
                # Pydantic 모델을 dict로 변환
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "tools": [t.model_dump() for t in tools_list]
                    }
                }

            if method == "notifications/initialized":
                return {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}

            return {"status": "ok", "message": "Server is ready."}
        except Exception as e:
            return {"error": str(e)}
    
    try:
        data = await request.json()
        message = types.JSONRPCMessage.model_validate(data)
        await global_writer.send(message)
        return {"status": "accepted"}
    except Exception as e:
        print(f"POST Error: {e}")
        return {"error": str(e)}

@app.post("/sse")
async def handle_sse_post(request: Request):
    return await forward_post_to_server(request)

@app.post("/messages")
async def handle_messages(request: Request):
    return await forward_post_to_server(request)

@app.post("/")
async def handle_root_post(request: Request):
    return await forward_post_to_server(request)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting Robust FastAPI MCP Server on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)