import os
import uvicorn
import asyncio
import traceback
import json
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

# --- [2. 도구(Tool) 실행 로직 분리 (핵심)] ---
# 라이브러리 거치지 않고 직접 실행할 함수입니다.
async def process_tool_call(name: str, arguments: dict) -> str:
    try:
        if name == "show_criteria":
            return get_criteria_info()

        elif name == "recommend_coffee":
            preference = arguments.get("preference", "")
            
            # [진단 1] 파일 존재 여부 확인
            BASE_DIR = os.path.dirname(os.path.abspath(__file__))
            DATA_FILE = os.path.join(BASE_DIR, 'coffee_clean.csv')
            if not os.path.exists(DATA_FILE):
                return f"🔥 [치명적 오류] 서버에 데이터 파일이 없습니다!\n(경로: {DATA_FILE})\nGitHub에 'coffee_clean.csv' 파일이 업로드되었는지 확인해주세요."

            # [진단 2] 실제 로직 실행 (타임아웃 보호)
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(get_coffee_recommendations, preference),
                    timeout=15.0
                )
            except asyncio.TimeoutError:
                return "Error: 처리 시간이 초과되었습니다 (15초)."
            
            # 결과 처리
            if isinstance(result, str):
                return result
            elif isinstance(result, dict):
                if result.get("type") == "recommendation":
                    output = [f"### ☕ '{preference}' 취향 추천 결과"]
                    output.append(f"_{result.get('flavor_desc', '')}_")
                    
                    countries = result.get('countries', [])
                    if not countries:
                        return "조건에 맞는 커피를 찾지 못했습니다."

                    for country in countries:
                        c_name = country.get('country_name', 'Unknown')
                        output.append(f"\n**[{c_name}]**")
                        for coffee in country.get('coffees', []):
                            c_name_item = coffee.get('name', 'Unknown')
                            c_rating = coffee.get('rating', '0')
                            c_desc = coffee.get('desc', '')[:100]
                            c_acid = coffee.get('acid', '')
                            output.append(f"- {c_name_item} ({c_rating}점)")
                            output.append(f"  특징: {c_desc}...")
                            output.append(f"  산미 : {c_acid}")
                    return "\n".join(output)
                else:
                    return result.get("content", "내용 없음")
            return str(result)

        else:
            return f"알 수 없는 도구입니다: {name}"

    except Exception as e:
        # 에러 발생 시 숨기지 않고 그대로 출력
        error_msg = f"시스템 내부 오류: {str(e)}\n{traceback.format_exc()}"
        print(f"🔥 Tool Error: {error_msg}")
        return error_msg

# --- [3. MCP 서버 도구 등록 (명세서용)] ---
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

# [중요] 내부 호출용 핸들러 (혹시 모를 대비)
@mcp_server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    result_text = await process_tool_call(name, arguments or {})
    return [types.TextContent(type="text", text=result_text)]

# --- [4. 수동 배관 및 라우팅 (Wiring)] ---

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
            await mcp_server.run(client_read, server_write, mcp_server.create_initialization_options())
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

# [핵심] 모든 요청을 직접 분류해서 처리하는 '수동 라우터'
async def forward_post_to_server(request: Request):
    global global_writer
    
    try:
        data = await request.json()
        method = data.get("method")
        msg_id = data.get("id")
        params = data.get("params", {})

        # 1. 초기화 및 상태 확인 (Handshake)
        if method == "initialize":
            print("👋 [Check] PlayMCP Initialize.")
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}, "prompts": {}, "resources": {}},
                    "serverInfo": {"name": "Coffee-Recommender", "version": "1.0"}
                }
            }
        if method == "ping": return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        
        # 2. 도구 목록 요청 (Tools List)
        if method == "tools/list":
            print("🛠️ [Check] Asking for Tools List.")
            tools_list = await handle_list_tools()
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": [t.model_dump() for t in tools_list]}}

        # 3. [여기가 정답] 도구 실행 요청 (Tools Call) - 직접 가로채서 실행!
        if method == "tools/call":
            print(f"⚡ [Action] Executing Tool: {params.get('name')}")
            
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            
            # 우리가 만든 함수 직접 호출
            result_text = await process_tool_call(tool_name, tool_args)
            
            # PlayMCP가 원하는 포맷으로 포장해서 즉시 리턴
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": result_text
                        }
                    ],
                    "isError": False if "오류" not in result_text else True
                }
            }

        # 4. 기타 알림
        if method == "notifications/initialized":
            return {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}

        # 5. 그 외 요청은 라이브러리로 토스 (fallback)
        if global_writer:
            message = types.JSONRPCMessage.model_validate(data)
            await global_writer.send(message)
            return {"status": "accepted"}
        
        return {"status": "ok", "message": "Server is ready."}

    except Exception as e:
        print(f"🔥 Request Handling Error: {e}")
        return {"error": str(e)}

@app.post("/sse")
async def handle_sse_post(request: Request): return await forward_post_to_server(request)

@app.post("/messages")
async def handle_messages(request: Request): return await forward_post_to_server(request)

@app.post("/")
async def handle_root_post(request: Request): return await forward_post_to_server(request)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting Direct-Dispatch MCP Server on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)