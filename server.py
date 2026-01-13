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
import concurrent.futures

# --- [1. 설정 및 앱 초기화] ---
app = FastAPI()
mcp_server = Server("Coffee-Recommender")
TIMEOUT_SECONDS = 15

# [보안 설정] PlayMCP 접속 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# [연결 고리] 봇 환경을 위한 전역 스트림 입구
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

# --- [3. 도구 실행 로직] ---
@mcp_server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent]:
    
    if name == "show_criteria":
        result = get_criteria_info()
        return [types.TextContent(type="text", text=result)]

    elif name == "recommend_coffee":
        preference = arguments.get("preference", "")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(get_coffee_recommendations, preference)
            try:
                result = future.result(timeout=TIMEOUT_SECONDS)
            except Exception as e:
                return [types.TextContent(type="text", text=f"Error: {str(e)}")]

        if isinstance(result, str):
            final_text = result
        elif isinstance(result, dict):
            if result.get("type") == "recommendation":
                output = [f"### ☕ '{preference}' 취향 추천 결과"]
                output.append(f"_{result.get('flavor_desc', '')}_")
                for country in result.get('countries', []):
                    c_name = country['country_name']
                    output.append(f"\n**[{c_name}]**")
                    for coffee in country['coffees']:
                        output.append(f"- {coffee['name']} ({coffee['rating']}점)")
                        output.append(f"  특징: {coffee['desc'][:100]}...")
                final_text = "\n".join(output)
            else:
                final_text = result.get("content", "내용 없음")
        else:
            final_text = str(result)
        return [types.TextContent(type="text", text=final_text)]

    raise ValueError(f"Unknown tool: {name}")

# --- [4. 수동 배관 작업 (Wiring)] ---

@app.get("/")
async def handle_root():
    return {"status": "ok", "message": "Coffee MCP Server is Running!"}

@app.get("/sse")
async def handle_sse(request: Request):
    global global_writer
    
    # 파이프 생성
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
        # [수정] 딕셔너리로 변환하여 전송
        yield {
            "event": "endpoint",
            "data": "/sse"
        }
        
        async with mcp_server.create_initialization_message() as init_msg:
            # [수정] Pydantic 모델을 dict로 변환
            yield init_msg.model_dump()
            
        async with server_read:
            async for message in server_read:
                # [수정] Pydantic 모델을 dict로 변환 (핵심 패치)
                if hasattr(message, 'model_dump'):
                    yield message.model_dump()
                else:
                    yield message

    return EventSourceResponse(event_generator())

# [최종 완결판] PlayMCP의 모든 찔러보기(Handshake)에 완벽 대응하는 코드
async def forward_post_to_server(request: Request):
    global global_writer
    
    # [시나리오 1] 연결 전: PlayMCP가 등록을 위해 이것저것 물어볼 때
    if global_writer is None:
        try:
            data = await request.json()
            method = data.get("method")
            msg_id = data.get("id") # 요청 ID (응답할 때 돌려줘야 함)
            
            # 1. "스펙 내놔봐(initialize)"
            if method == "initialize":
                print("👋 [Check] PlayMCP Initialize Handshake.")
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "tools": {},
                            "prompts": {},
                            "resources": {}
                        },
                        "serverInfo": {
                            "name": "Coffee-Recommender",
                            "version": "1.0"
                        }
                    }
                }
            
            # 2. "살아있니(ping)?"
            if method == "ping":
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {}
                }

            # 3. [추가됨] "도구 목록 줘봐(tools/list)" - 여기가 핵심!
            if method == "tools/list":
                print("🛠️ [Check] PlayMCP asking for Tools List.")
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "tools": [
                            {
                                "name": "show_criteria",
                                "description": "커피 추천 기준과 로직(산미, 고소함 등)을 보여줍니다.",
                                "inputSchema": {"type": "object", "properties": {}}
                            },
                            {
                                "name": "recommend_coffee",
                                "description": "사용자의 취향(예: 산미, 고소함, 과일향 등)을 입력받아 알맞은 커피를 추천합니다.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "preference": {
                                            "type": "string",
                                            "description": "사용자의 커피 취향 (예: '산미 있는거', '고소한 맛')"
                                        }
                                    },
                                    "required": ["preference"]
                                }
                            }
                        ]
                    }
                }

            # 4. "초기화 완료 알림(notifications/initialized)" - 응답 필요 없음
            if method == "notifications/initialized":
                print("✅ [Check] Client Initialized.")
                return {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}

            # 그 외 (알 수 없는 요청은 그냥 OK 처리)
            print(f"👀 [Check] Unknown Probe: {method}")
            return {"status": "ok", "message": "Server is ready."}
            
        except Exception as e:
            print(f"Probe Error: {e}")
            return {"error": str(e)}
    
    # [시나리오 2] 연결 후: 실제 채팅 메시지 처리
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
    print(f"🚀 Starting Fixed FastAPI MCP Server on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)