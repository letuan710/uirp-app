---
name: translate_query
version: 1
tier: S
output: text
description: Dịch từ khóa tìm kiếm ngắn sang ngôn ngữ đích để tìm trên nền tảng nước ngoài (ADR-012)
---
Dịch cụm từ khóa tìm kiếm dưới đây sang $lang, giữ nguyên tên riêng/tên thương hiệu nếu có.
Đây là TỪ KHÓA TÌM KIẾM (không phải văn bản đầy đủ) — trả về NGẮN GỌN, tự nhiên, đúng cách
người bản xứ gõ khi tìm kiếm. KHÔNG thêm dấu ngoặc kép, KHÔNG giải thích, KHÔNG bình luận.
Chỉ trả về đúng từ khóa đã dịch.

Từ khóa gốc:
"""
$text
"""
