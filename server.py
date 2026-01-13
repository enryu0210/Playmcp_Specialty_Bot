import os
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware # [핵심] 보안 설정 모듈
from sse_starlette.sse import EventSourceResponse
from mcp.server.sse import SseServerTransport
from mcp.server import Server
import mcp.types as types
from coffee_tools import get_coffee_recommendations, get_criteria_info
import concurrent.futures

# --- [1. 설정 및 앱 초기화] ---
app = FastAPI()
mcp_server = Server("Coffee-Recommender")
TIMEOUT_SECONDS = 15

# [핵심 해결책] CORS 미들웨어 추가
# PlayMCP(외부)가 내 서버의 응답을 읽을 수 있도록 허용하는 '통행증'입니다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 모든 주소에서 접속 허용 (카카오 포함)
    allow_credentials=True,
    allow_methods=["*"],  # 모든 HTTP 메서드(GET, POST 등) 허용
    allow_headers=["*"],  # 모든 헤더 허용
)

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

# --- [4. PlayMCP 연결 경로 설정 (CORS + Path 완벽 대응)] ---

@app.get("/")
async def handle_root():
    return {"status": "ok", "message": "Coffee MCP Server is Running!"}

@app.get("/sse")
async def handle_sse(request: Request):
    """MCP 연결 요청 처리 (GET) - 듣기"""
    async with mcp_server.create_initialization_message() as init_msg:
        async def event_generator():
            yield init_msg
            async for message in mcp_server.list_tools():
                yield message
            
            # [중요] POST 요청은 '/sse'로 다시 보내라고 PlayMCP에게 알려줍니다.
            # (원래는 /messages지만, PlayMCP가 /sse로 쏘는 경우를 대비해 통일)
            transport = SseServerTransport("/sse")
            
            async with transport.connect(request.scope, request.receive, request._send) as (read_stream, write_stream):
                await mcp_server.run(read_stream, write_stream, mcp_server.create_initialization_options())
        
        return EventSourceResponse(event_generator())

# [핵심] PlayMCP가 POST를 /sse로 보내든 /messages로 보내든 다 처리함
@app.post("/sse")
async def handle_sse_post(request: Request):
    return await mcp_server.process_request(request)

@app.post("/messages")
async def handle_messages(request: Request):
    return await mcp_server.process_request(request)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting CORS-Enabled FastAPI MCP Server on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)