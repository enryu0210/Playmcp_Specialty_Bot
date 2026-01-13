import pandas as pd
import os

# [수정 1] 절대 경로 설정 (Render 배포 시 필수)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'coffee_clean.csv')

MAJOR_COUNTRIES = [
    'Ethiopia', 'Kenya', 'Colombia', 'Brazil', 'Panama', 'Guatemala', 
    'Costa Rica', 'Indonesia', 'Honduras', 'El Salvador', 'Peru', 'Rwanda',
    'Mexico', 'Uganda', 'Tanzania', 'Nicaragua', 'Yemen', 'Sumatra', 'India', 'Vietnam'
]

# [수정 2] 전역 변수에 데이터 미리 로드
def load_data_once():
    """서버 시작 시 데이터를 한 번만 로드"""
    if not os.path.exists(DATA_FILE):
        print(f"Error: 파일이 없습니다 - {DATA_FILE}")
        return None

    try:
        try:
            df = pd.read_csv(DATA_FILE, encoding='utf-8')
        except UnicodeDecodeError:
            try:
                df = pd.read_csv(DATA_FILE, encoding='cp949')
            except UnicodeDecodeError:
                df = pd.read_csv(DATA_FILE, encoding='latin1')

        df['desc_1'] = df['desc_1'].fillna('').astype(str)
        
        cols_to_numeric = ['acid', 'body', 'flavor', 'aftertaste', 'aroma', 'rating']
        for col in cols_to_numeric:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        def extract_country(origin_text):
            if not isinstance(origin_text, str): return "Other"
            for country in MAJOR_COUNTRIES:
                if country.lower() in origin_text.lower():
                    return country
            return "Other"
            
        df['country'] = df['origin'].apply(extract_country)
        print("✅ Data Loaded Successfully!")
        return df

    except Exception as e:
        print(f"DEBUG: Data Load Error: {e}")
        return None

# 전역 변수로 선언 (최초 1회 실행)
GLOBAL_DF = load_data_once()

def get_criteria_info() -> str:
    """분류 기준 텍스트 반환"""
    return (
        "### 🔍 커피 추천 로직 및 분류 기준\n\n"
        "**1. 산미 (Acidic)**\n"
        "- **과일 계열 (Fruity)**: 산미 점수 9점 이상 + (Berry, Citrus, Fruit 키워드)\n"
        "- **꽃향 계열 (Floral)**: 산미 점수 9점 이상 + (Floral, Jasmine, Rose 키워드)\n"
        "- 🚫 제외: 흙내(Earthy), 담배(Tobacco) 등 텁텁한 표현\n"
        "- 🏳️ 추천 국가: 에티오피아, 파나마, 케냐\n\n"
        "**2. 고소한 맛 (Nutty)**\n"
        "- **조건**: 산미 점수 8점 이하\n"
        "- 🚫 제외: 시큼함(Tart), 와인(Wine), 톡 쏘는 산미(Bright/Citrus)\n"
        "- 🏳️ 추천 국가: 브라질, 콜롬비아, 과테말라, 인도네시아\n\n"
        "※ 위 조건을 만족하는 그룹 내에서 **평점(Rating)**이 높은 순서대로 추천합니다."
    )

def get_coffee_recommendations(preference: str):
    # [핵심 수정] Fail-safe: '추천' 도구로 '기준' 질문이 들어왔을 때 납치하여 처리
    check_keywords = ["기준", "어떻게", "원리", "알려줘", "설명", "로직", "분류"]
    if any(word in preference for word in check_keywords) and len(preference) < 15:
        # 길이가 너무 길면(복합 질문이면) 무시하고, 짧은 질문("기준 알려줘")일 때만 작동
        return {"type": "info", "content": get_criteria_info()}

    df = load_data_once()
    if df is None:
        return "Error: 데이터 파일을 찾을 수 없습니다."

    # --- 취향 분석 ---
    target_type = None
    keywords = []
    exclude_keywords = []
    priority_countries = []
    flavor_desc = ""
    pref_lower = preference.lower()

    # (A) 산미 (Acidic)
    if any(word in pref_lower for word in ["산미", "신맛", "상큼", "과일", "화사", "꽃", "플로럴", "향기", "floral", "베리", "시트러스", "fruit"]):
        df = df[df['acid'] >= 9.0].copy()
        exclude_keywords = ['earthy', 'tobacco', 'smoke', 'ash', 'leather', 'musty', 'rubber']
        priority_countries = ['Ethiopia', 'Panama', 'Kenya']
        
        if any(word in pref_lower for word in ["꽃", "플로럴", "자스민", "향기", "floral"]):
            target_type = 'floral'
            keywords = ['floral', 'jasmine', 'rose', 'lily', 'blossom', 'lavender', 'tea-like', 'lemongrass', 'magnolia', 'hibiscus']
            flavor_desc = "은은한 꽃향기와 화사한 산미 (Floral & High Acid)"
        elif any(word in pref_lower for word in ["과일", "베리", "시트러스", "레몬", "사과", "fruit"]):
            target_type = 'fruity'
            keywords = ['fruit', 'berry', 'citrus', 'lemon', 'orange', 'apple', 'grape', 'peach', 'stone fruit', 'tropical']
            flavor_desc = "상큼 달콤한 과일의 풍미 (Fruity & High Acid)"
        else:
            target_type = 'general_acidic'
            keywords = ['acid', 'fruit', 'floral', 'bright']
            flavor_desc = "화사한 산미와 과일향 (High Acid, No Earthy)"

    # (B) 고소 (Nutty)
    elif any(word in pref_lower for word in ["고소", "견과", "구수", "묵직", "초콜릿", "바디", "쓴맛"]):
        target_type = 'nutty'
        df = df[df['acid'] <= 8.0].copy()
        exclude_keywords = ['bright', 'tart', 'citrus', 'lemon', 'lime', 'grapefruit', 'wine', 'sour', 'vinegar']
        keywords = ['nut', 'chocolate', 'cocoa', 'almond', 'walnut', 'savory', 'caramel', 'toffee', 'body']
        priority_countries = ['Brazil', 'Colombia', 'Guatemala', 'Indonesia', 'India']
        flavor_desc = "고소하고 묵직한 바디감 (Low Acid, No Citrus)"
    else:
        # 취향을 알 수 없을 때도 기준 정보를 슬쩍 보여줌
        return {
            "type": "error", 
            "content": "죄송합니다. '고소한 맛', '과일 같은 산미', '꽃향기' 등으로 질문해 주세요.\n(궁금하시다면 '추천 기준'이라고 물어봐 주세요.)"
        }

    # --- 필터링 ---
    if exclude_keywords:
        exclude_pattern = '|'.join(exclude_keywords)
        df = df[~df['desc_1'].str.contains(exclude_pattern, case=False, na=False)]

    if keywords:
        pattern = '|'.join(keywords)
        df['keyword_match'] = df['desc_1'].str.contains(pattern, case=False, na=False)
        if df['keyword_match'].sum() > 5:
            df = df[df['keyword_match']].copy()

    if df.empty:
        return "조건에 맞는 커피를 찾을 수 없습니다."

    # --- 국가별 그룹화 ---
    available_countries = df['country'].unique()
    selected_countries = []
    
    for p_country in priority_countries:
        if p_country in available_countries:
            selected_countries.append(p_country)
            
    if len(selected_countries) < 3:
        country_ratings = df.groupby('country')['rating'].mean().sort_values(ascending=False)
        for country in country_ratings.index:
            if country not in selected_countries and country != "Other":
                selected_countries.append(country)
                if len(selected_countries) >= 3:
                    break
    
    # --- 결과 구성 ---
    results = {
        "type": "recommendation",
        "flavor_desc": flavor_desc,
        "countries": []
    }

    for country in selected_countries:
        top_coffees = df[df['country'] == country].sort_values(by='rating', ascending=False).head(2)
        
        coffee_list = []
        for _, row in top_coffees.iterrows():
            coffee_list.append({
                "name": row['name'],
                "rating": row['rating'],
                "desc": row['desc_1'],
                # [중요] 시각화용 데이터 전달
                "aroma" : row['aroma'],
                "acid": row['acid'],
                "body": row['body'],
                "flavor": row['flavor'],
                "aftertaste": row['aftertaste']
            })
        results["countries"].append({"country_name": country, "coffees": coffee_list})

    return results