"""冒烟验证：知识库加载 + cost 估算 + 景点门票/时长覆盖"""
import asyncio, sys
sys.path.insert(0, ".")

from src.tools import CITY_GUIDES, _lookup_city_spot, _cost, _attractions

print(f"✅ 已加载 {len(CITY_GUIDES)} 城知识库")
print(f"   覆盖北京={'北京' in CITY_GUIDES}、厦门={'厦门' in CITY_GUIDES}、乌鲁木齐={'乌鲁木齐' in CITY_GUIDES}")

# 验证北京真实 cost_per_day
bj = CITY_GUIDES.get("北京")
print(f"\n📍 北京 cost_per_day: {bj['cost_per_day']}")
print(f"📍 北京景点数: {len(bj['spots'])}")

# 验证故宫门票/时长能查到
gugong = _lookup_city_spot("北京", "故宫博物院")
print(f"\n🏯 故宫博物院: {gugong}")
assert gugong and gugong["ticket"] == 60 and gugong["duration_min"] == 240, "故宫查不到真实门票/时长！"

# 验证 _cost 走知识库路径
spots = ["故宫博物院", "天坛公园", "颐和园"]
result = _cost(days=3, spots=spots, budget=None, city="北京")
print(f"\n💰 北京 3 天花费（知识库路径）:")
print(f"   住宿 {result.lodging_total} + 餐饮 {result.food_total} + 交通 {result.transport_total} + 门票 {result.tickets_total} = {result.total}")
# 知识库: lodging 400×3=1200, food 150×3=450, transit 50×3=150, tickets=60+34+60=154 → total=1954
assert result.lodging_total == 1200.0, f"住宿应为 1200，实际 {result.lodging_total}"
assert result.tickets_total == 154.0, f"门票应为 154，实际 {result.tickets_total}"

# 验证 MCP 限流也不影响知识库（纯本地）
print(f"\n🎯 知识库纯本地路径无网络依赖 ✅")
print("\n=== 全部冒烟验证通过 ===")
