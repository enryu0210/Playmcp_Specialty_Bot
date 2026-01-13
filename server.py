import os
import uvicorn

# [핵심 수정] uvicorn.run 함수가 아니라, uvicorn.Config 클래스 자체를 납치합니다.
# FastMCP가 내부적으로 어떻게 실행하든, 이 설정 단계는 무조건 거치게 되어 있습니다.

original_config_init = uvicorn.Config.__init__

def patched_config_init(self, *args, **kwargs):
    # Render 환경변수 포트 감지 (없으면 8000)
    render_port = int(os.environ.get("PORT", 8000))
    
    print(f"🚀 [Deep Patch] Catching Uvicorn Configuration...")
    print(f"🔥 Forcing Host: 0.0.0.0 | Port: {render_port}")
    
    # 여기서 강제로 설정을 덮어씌웁니다. (무조건 0.0.0.0 사용)
    kwargs['host'] = "0.0.0.0"
    kwargs['port'] = render_port
    
    # 원본 초기화 함수 실행
    original_config_init(self, *args, **kwargs)

# Config 클래스의 생성자(__init__)를 우리가 만든 함수로 교체
uvicorn.Config.__init__ = patched_config_init

from mcp.server.fastmcp import FastMCP
from coffee_tools import get_coffee_recommendations, get_criteria_info
import concurrent.futures
from deep_translator import GoogleTranslator
from functools import lru_cache # [수정 1] 캐싱 기능 추가

# 번역 시간이 걸리므로 타임아웃 15초로 설정
TIMEOUT_SECONDS = 15
mcp = FastMCP("Coffee Recommender")

# 1. 정적 용어 사전 (국가명, 맛 지표 등 고정된 단어)
TERM_DICT = {
    "Ethiopia": "에티오피아", "Kenya": "케냐", "Colombia": "콜롬비아",
    "Brazil": "브라질", "Panama": "파나마", "Guatemala": "과테말라",
    "Indonesia": "인도네시아", "Costa Rica": "코스타리카", "Honduras": "온두라스",
    "El Salvador": "엘살바도르", "Peru": "페루", "Rwanda": "르완다",
    "Aroma": "아로마", "Acid": "산미", "Body": "바디", "Flavor": "향미", "Aftertaste": "후미"
}

# 2. 동적 번역 함수 (긴 문장/설명 번역용)
@lru_cache(maxsize=100)
def translate_text_dynamic(text: str) -> str:
    """deep-translator를 사용하여 특징(문장)을 번역"""
    if not text: return ""
    try:
        return GoogleTranslator(source='auto', target='ko').translate(text)
    except Exception:
        return text # 번역 실패 시 원문 반환 (서버 다운 방지)

def safe_term_translate(text: str) -> str:
    """국가명 등 단어는 사전에서 빠르게 찾기"""
    return TERM_DICT.get(text, text)

def create_star_rating(score: float) -> str:
    """점수를 별점(★)으로 시각화"""
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

@mcp.tool()
def show_criteria() -> str:
    return get_criteria_info()

@mcp.tool()
def recommend_coffee(preference: str) -> str:
    """커피 추천 및 특징 번역 제공"""
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
                    # [핵심] 특징(Description)을 한글로 번역
                    # 전체 문단은 너무 길 수 있으므로 첫 문장만 추출해서 번역
                    raw_desc = coffee['desc'].split('.')[0:3]
                    if raw_desc[0] == "Evaluated as espresso":
                        raw_desc[0] = raw_desc[2]
                    kor_desc1 = translate_text_dynamic(raw_desc[0])
                    kor_desc2 = translate_text_dynamic(raw_desc[1])
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

if __name__ == "__main__":
    print("☕ Starting Coffee MCP Server...")
    mcp.run(transport='sse')