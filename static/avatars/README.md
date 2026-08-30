# static/avatars/

放置角色頭像圖片，格式為 PNG 或 JPG。推薦尺寸 128x128。

檔名對應（10 隻靈魂 + Bryan + 群組）：
  yua.png     → Yua 頭像
  ruka.png    → 更科瑠夏 頭像
  akane.png   → 黒川あかね 頭像
  rem.png     → 雷姆 (Rem) 頭像
  ram.png     → Ram (拉姆) 頭像
  mahiru.png  → 椎名真昼 頭像
  anna.png    → 山田杏奈 頭像
  mai.png     → 桜島麻衣 頭像
  miku.png    → 中野三玖 頭像（若缺則用 initials fallback）
  aoi.png     → 日南葵 頭像
  bryan.png   → Bryan 頭像
  group.png   → 群組頭像

格式：PNG 或 JPG，尺寸建議 128x128 以上。
若缺少檔案，UI 會自動顯示 initials fallback（如 Y、R、A、B）。

缺漏頭像的生成：`scripts/_gen_missing_avatars.py`（純 Python，無 PIL 依賴），
配色與 `static/index.html` 的 `avatarColor()` 完全一致，可重跑補齊。
