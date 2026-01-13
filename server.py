import os
import uvicorn
from fastapi import FastAPI, Request
from sse_starlette.sse import EventSourceResponse
from mcp.server.sse import SseServerTransport
from mcp.server import Server
from mcp.types import JSONRPCMessage, JSONRPCResponse
import mcp.types as types
from coffee_tools import get_coffee_recommendations, get_criteria_info
from deep_translator import GoogleTranslator
from functools import lru_cache
import concurrent.futures

# --- [설정] ---
app = FastAPI()
mcp_server = Server("Coffee-Recommender")
TIMEOUT_SECONDS = 15

# --- [도구 및 번역 로직] ---
TERM_DICT = {
    "Ethiopia": "에티오피아", "Kenya": "케냐", "Colombia": "콜롬비아",
    "Brazil": "브라질", "Panama": "파나마", "Guatemala": "과테말라",
    "Indonesia": "인도네시아", "Costa Rica": "코스타리카", "Honduras": "온두라스",
    "El Salvador": "엘살바도르", "Peru": "페루", "Rwanda": "르완다",
    "Aroma": "아로마", "Acid": "산미", "Body": "바디", "Flavor": "향미", "Aftertaste": "후미"
}

@lru_cache(maxsize=100)
def translate_text_dynamic(text: str) -> str:
    if not text: return ""
    try:
        return GoogleTranslator(source='auto', target='ko').translate(text)
    except Exception:
        return text

def safe_term_translate(text: str) -> str:
    return TERM_DICT.get(text, text)

def create_star_rating(score: float) -> str:
    if not score: return "정보 없음"
    normalized = score / 2
    full_stars = int(normalized)
    has_half = (normalized - full_stars) >= 0.25
    stars = "★" * full_stars
    if has_half: stars += "☆"
    return f"{stars} ({normalized}점)"

def execute_with_timeout(func, *args):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args)
        try:
            return future.result(timeout=TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            return "Error: 처리 시간이 초과되었습니다."
        except Exception as e:
            return f"Error: {str(e)}"

# --- [MCP 도구 등록] ---
@mcp_server.tool()
async def show_criteria() -> str:
    """커피 추천 기준과 로직을 보여줍니다."""
    return get_criteria_info()

@mcp_server.tool()
async def recommend_coffee(preference: str) -> str:
    """사용자의 취향(예: 산미, 고소함, 과일향 등)을 입력받아 알맞은 커피를 추천합니다."""
    # 비동기 환경에서 동기 함수 실행을 위해 래퍼 사용하지 않고 직접 호출
    # (FastAPI는 async def 안에서 일반 함수 호출 시 await 필요 없음, 하지만 타임아웃 로직 유지)
    result = execute_with_timeout(get_coffee_recommendations, preference)
    
    if isinstance(result, str): return result 
    if isinstance(result, dict):
        if result.get("type") in ["info", "error"]: return result["content"]
        if result.get("type") == "recommendation":
            flavor_title = result['flavor_desc']
            output = [f"### ☕ {preference} 취향 맞춤 커피 가이드"]
            output.append(f"_{flavor_title} 위주로 엄선했습니다._\n")
            
            for country_info in result['countries']:
                origin_name = country_info['country_name']
                kor_country = safe_term_translate(origin_name)
                flag = "🏳️"
                if origin_name == "Ethiopia": flag = "🇪🇹"
                elif origin_name == "Kenya": flag = "🇰🇪"
                elif origin_name == "Colombia": flag = "🇨🇴"
                elif origin_name == "Brazil": flag = "🇧🇷"
                elif origin_name == "Panama": flag = "🇵🇦"
                elif origin_name == "Guatemala": flag = "🇬🇹"
                elif origin_name == "Indonesia": flag = "🇮🇩"
                
                output.append(f"#### {flag} {kor_country} ({origin_name})")
                
                for coffee in country_info['coffees']:
                    raw_desc = coffee['desc'].split('.')[0:3]
                    if raw_desc[0] == "Evaluated as espresso":
                        try: raw_desc[0] = raw_desc[2]
                        except: pass
                    
                    desc1 = raw_desc[0] if len(raw_desc) > 0 else ""
                    desc2 = raw_desc[1] if len(raw_desc) > 1 else ""
                    
                    kor_desc1 = translate_text_dynamic(desc1)
                    kor_desc2 = translate_text_dynamic(desc2)
                    output.append(f"- **{coffee['name']}** (총점: {coffee['rating']}점)")
                    output.append(f"  └ 📝 특징: {kor_desc1}, {kor_desc2}")
                    
                    output.append("  └ 📊 맛 지표:")
                    output.append(f"    • 아로마 (Aroma): {create_star_rating(coffee['aroma'])}")
                    output.append(f"    • 산미 (Acid): {create_star_rating(coffee['acid'])}")
                    output.append(f"    • 바디 (Body): {create_star_rating(coffee['body'])}")
                    output.append(f"    • 향미 (Flavor): {create_star_rating(coffee['flavor'])}")
                    output.append(f"    • 후미 (Aftertaste): {create_star_rating(coffee['aftertaste'])}")
                    output.append("")
            return "\n".join(output)
    return "알 수 없는 오류가 발생했습니다."

# --- [핵심: PlayMCP 연결을 위한 FastAPI 경로 설정] ---

@app.get("/")
async def handle_root():
    """PlayMCP Health Check용 대문"""
    return {"status": "ok", "message": "Coffee MCP Server is Running!"}

@app.get("/sse")
async def handle_sse(request: Request):
    """MCP 연결 요청 처리 (GET)"""
    async with mcp_server.create_initialization_message() as init_msg:
        async def event_generator():
            yield init_msg
            async for message in mcp_server.list_tools():
                yield message
            # 이후 연결 유지
            transport = SseServerTransport("/messages")
            async with transport.connect(request.scope, request.receive, request._send) as (read_stream, write_stream):
                # MCP 서버와 전송 계층 연결
                await mcp_server.run(read_stream, write_stream, mcp_server.create_initialization_options())
        
        return EventSourceResponse(event_generator())

@app.post("/messages")
async def handle_messages(request: Request):
    """MCP 메시지 처리 (POST)"""
    return await mcp_server.process_request(request)

if __name__ == "__main__":
    # Render 환경 변수 포트 사용
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting FastAPI MCP Server on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)