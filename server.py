import os
import uvicorn
from fastapi import FastAPI, Request
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

# --- [2. 도구(Tool) 정의] ---
# 표준 Server 방식에서는 도구 목록을 이렇게 명시적으로 알려줘야 합니다.
@mcp_server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="show_criteria",
            description="커피 추천 기준과 로직(산미, 고소함 등)을 보여줍니다.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
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

# --- [3. 도구 실행 로직 연결] ---
# 요청이 들어오면 여기서 함수를 실행합니다.
@mcp_server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent]:
    
    if name == "show_criteria":
        # 동기 함수 실행을 위한 처리
        result = get_criteria_info()
        return [types.TextContent(type="text", text=result)]

    elif name == "recommend_coffee":
        preference = arguments.get("preference", "")
        
        # 타임아웃을 적용하여 실행
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(get_coffee_recommendations, preference)
            try:
                result = future.result(timeout=TIMEOUT_SECONDS)
            except Exception as e:
                return [types.TextContent(type="text", text=f"Error: {str(e)}")]

        # 결과 처리 (딕셔너리 -> 텍스트 변환)
        if isinstance(result, str):
            final_text = result
        elif isinstance(result, dict):
            if result.get("type") == "recommendation":
                # (기존의 예쁘게 꾸미는 로직을 여기에 간략히 포함하거나, 
                # coffee_tools에서 텍스트로 완성해서 받는게 좋지만, 
                # 여기서는 핵심 데이터만 텍스트로 변환해서 보냅니다.)
                # *주의: 번역 기능 등이 필요하면 기존 로직을 가져와야 합니다.
                # 편의를 위해 coffee_tools가 텍스트를 반환하도록 유도하거나 간단히 처리합니다.
                
                # [간소화된 응답 생성] - 복잡한 번역 로직은 서버 부하 줄이기 위해 생략 가능
                # 만약 기존의 '번역된 예쁜 출력'을 원하시면 server.py에 로직을 다시 넣어야 합니다.
                # 여기서는 핵심 정보 전달에 집중한 버전을 제공합니다.
                
                output = [f"### ☕ '{preference}' 취향 추천 결과"]
                output.append(f"_{result.get('flavor_desc', '')}_")
                
                for country in result.get('countries', []):
                    c_name = country['country_name']
                    output.append(f"\n**[{c_name}]**")
                    for coffee in country['coffees']:
                        output.append(f"- {coffee['name']} ({coffee['rating']}점)")
                        output.append(f"  특징: {coffee['desc'][:100]}...") # 긴 설명 자르기
                
                final_text = "\n".join(output)
            else:
                final_text = result.get("content", "내용 없음")
        else:
            final_text = str(result)

        return [types.TextContent(type="text", text=final_text)]

    raise ValueError(f"Unknown tool: {name}")

# --- [4. PlayMCP 연결을 위한 FastAPI 경로 설정] ---

@app.get("/")
async def handle_root():
    """PlayMCP Health Check용 대문 (이제 404 안 뜸!)"""
    return {"status": "ok", "message": "Coffee MCP Server is Running!"}

@app.get("/sse")
async def handle_sse(request: Request):
    """MCP 연결 요청 처리 (GET) - 이제 405 안 뜸!"""
    async with mcp_server.create_initialization_message() as init_msg:
        async def event_generator():
            yield init_msg
            async for message in mcp_server.list_tools():
                yield message
            # 이후 연결 유지
            transport = SseServerTransport("/messages")
            async with transport.connect(request.scope, request.receive, request._send) as (read_stream, write_stream):
                await mcp_server.run(read_stream, write_stream, mcp_server.create_initialization_options())
        
        return EventSourceResponse(event_generator())

@app.post("/messages")
async def handle_messages(request: Request):
    """MCP 메시지 처리 (POST)"""
    return await mcp_server.process_request(request)

# [추가됨] PlayMCP가 /sse 주소로 POST를 날려도 받아주도록 처리
@app.post("/sse")
async def handle_sse_post(request: Request):
    """PlayMCP 호환성: /sse로 들어오는 POST도 처리"""
    return await mcp_server.process_request(request)

# [추가됨] 혹시 메인 주소(/)로 POST를 날려도 처리
@app.post("/")
async def handle_root_post(request: Request):
    return await mcp_server.process_request(request)

if __name__ == "__main__":
    # Render 환경 변수 포트 사용
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting Standard FastAPI MCP Server on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)