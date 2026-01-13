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

# [보안 설정] PlayMCP가 접속할 수 있도록 허용 (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# [핵심] GET(연결)과 POST(전송)를 이어주는 '전역 연결 고리'
# 봇(단일 인스턴스) 환경이므로 전역 변수로 스트림 입구를 관리합니다.
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

# --- [4. 수동 배관 작업 (Wiring) - 에러 원천 차단] ---

@app.get("/")
async def handle_root():
    return {"status": "ok", "message": "Coffee MCP Server is Running!"}

@app.get("/sse")
async def handle_sse(request: Request):
    """MCP 연결 요청 처리 (GET) - 듣기 모드"""
    global global_writer
    
    # 1. 서버와 통신할 파이프(Stream) 직접 생성
    # client_read, client_write: 클라이언트 -> 서버 (POST 데이터 이동 통로)
    # server_read, server_write: 서버 -> 클라이언트 (SSE 이벤트 이동 통로)
    client_write, client_read = create_memory_object_stream(10)
    server_write, server_read = create_memory_object_stream(10)
    
    # 2. POST 요청이 오면 데이터를 넣을 입구를 전역 변수에 저장
    global_writer = client_write

    # 3. 백그라운드에서 MCP 서버 실행 (통신 시작)
    async def run_mcp_server():
        try:
            # 여기서 server.run을 직접 돌립니다. (process_request 같은거 안 씀)
            await mcp_server.run(
                client_read, 
                server_write, 
                mcp_server.create_initialization_options()
            )
        except Exception as e:
            print(f"Server Error: {e}")

    asyncio.create_task(run_mcp_server())

    # 4. SSE 이벤트 생성기
    async def event_generator():
        # PlayMCP에게 "여기로 데이터 보내세요"라고 알려주는 이벤트
        yield {
            "event": "endpoint",
            "data": "/sse"
        }
        
        # 초기화 메시지 전송
        async with mcp_server.create_initialization_message() as init_msg:
            yield init_msg
            
        # 서버에서 나오는 메시지를 실시간으로 전송
        async with server_read:
            async for message in server_read:
                yield message

    return EventSourceResponse(event_generator())

# [핵심] 모든 POST 요청을 처리하는 통합 핸들러
async def forward_post_to_server(request: Request):
    global global_writer
    if global_writer is None:
        return {"error": "No active SSE connection found. Please connect to GET /sse first."}
    
    try:
        data = await request.json()
        message = types.JSONRPCMessage.model_validate(data)
        # 파이프를 통해 직접 밀어넣음
        await global_writer.send(message)
        return {"status": "accepted"}
    except Exception as e:
        print(f"POST Error: {e}")
        return {"error": str(e)}

# PlayMCP가 찌르는 모든 구멍을 다 막아서 처리
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
    print(f"🚀 Starting Manual-Wired FastAPI MCP Server on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)