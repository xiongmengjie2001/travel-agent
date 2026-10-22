"""一次性脚本：把 data/cities/*.json 按城市线级分目录存放。"""
import json, os, shutil

BASE = os.path.join(os.path.dirname(__file__), "data", "cities")

TIER1 = {"北京", "上海", "广州", "深圳"}
TIER1_5 = {"成都", "杭州", "重庆", "西安", "苏州", "武汉", "南京", "天津", "长沙", "郑州", "东莞", "青岛", "沈阳", "宁波", "昆明"}
TIER2 = {"合肥", "佛山", "厦门", "福州", "无锡", "济南", "大连", "哈尔滨", "长春", "温州",
         "石家庄", "南宁", "贵阳", "南昌", "太原", "烟台", "珠海", "中山", "惠州", "潍坊",
         "常州", "绍兴", "金华", "南通", "嘉兴", "保定", "兰州", "乌鲁木齐", "呼和浩特"}

mapping = {}
for c in TIER1: mapping[c] = "tier1"
for c in TIER1_5: mapping[c] = "tier1_5"
for c in TIER2: mapping[c] = "tier2"

for name in ("tier1", "tier1_5", "tier2"):
    os.makedirs(os.path.join(BASE, name), exist_ok=True)

moved = 0
unclassified = []
for fname in os.listdir(BASE):
    if not fname.endswith(".json"):
        continue
    city = fname[:-5]
    target_dir = mapping.get(city)
    if target_dir is None:
        unclassified.append(city)
        continue
    src = os.path.join(BASE, fname)
    dst = os.path.join(BASE, target_dir, fname)
    shutil.move(src, dst)
    moved += 1

print(f"已移动 {moved} 个城市文件到 tier1/tier1_5/tier2/")
if unclassified:
    print(f"⚠️ 未分类（留在根目录）: {unclassified}")
