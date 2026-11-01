"""名片 OCR 辨識資料庫(card_scanner):在瀏覽器裡拍照辨識名片、存成可快速搜尋的名片庫。

整個 App 是一個自給自足的靜態 HTML 頁面(web/ 下的 JS / CSS 在建置時內嵌),資料存在使用者
瀏覽器的 IndexedDB,不經過任何伺服器;OCR 用 Tesseract.js(本機)或 Claude 視覺模型(選用)。
"""
