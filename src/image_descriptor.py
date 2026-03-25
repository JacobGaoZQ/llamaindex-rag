"""
图片描述生成模块
使用多模态模型（通义千问-VL）生成图片描述
"""
import os
import json
import base64
import hashlib
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict
from pathlib import Path

import httpx


@dataclass
class ImageDescription:
    """图片描述结果"""
    image_id: str
    description: str           # 图片内容描述
    keywords: List[str]        # 关键词列表
    category: str              # 图片类别（流程图、示意图、实物图等）
    ocr_text: Optional[str]    # OCR 识别文字（如有）
    confidence: float          # 描述置信度


class ImageDescriptor:
    """
    图片描述生成器

    支持的多模态模型：
    - qwen-vl-max: 通义千问视觉语言模型（推荐，效果最好）
    - qwen-vl-plus: 通义千问视觉语言模型（性价比）
    """

    def __init__(self, api_key: str, model: str = "qwen-vl-max",
                 cache_dir: str = "./image_desc_cache"):
        """
        初始化图片描述生成器

        Args:
            api_key: DashScope API Key
            model: 视觉语言模型名称
            cache_dir: 描述缓存目录
        """
        self.api_key = api_key
        self.model = model
        self.cache_dir = cache_dir
        self.api_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

        # 创建缓存目录
        os.makedirs(cache_dir, exist_ok=True)

    def describe_image(self, image_path: str, image_id: str,
                       context: str = "") -> ImageDescription:
        """
        生成图片描述

        Args:
            image_path: 图片文件路径
            image_id: 图片唯一标识
            context: 图片上下文（前后文文本）

        Returns:
            ImageDescription: 图片描述结果
        """
        # 检查缓存
        cached = self._get_cached_description(image_path)
        if cached:
            cached.image_id = image_id  # 更新 image_id
            return cached

        # 读取图片并编码为 base64
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode()

        # 获取图片格式
        image_ext = Path(image_path).suffix.lower().lstrip('.')
        if image_ext == 'jpg':
            image_ext = 'jpeg'

        # 构建提示词
        prompt = self._build_prompt(context)

        # 调用多模态 API
        response = self._call_api(image_data, prompt, image_ext)

        # 解析响应
        result = self._parse_response(image_id, response)

        # 缓存结果
        self._cache_description(image_path, result)

        return result

    def _build_prompt(self, context: str) -> str:
        """构建描述生成提示词"""
        base_prompt = """请仔细分析这张图片，并提供以下信息：

1. **图片描述**：详细描述图片的内容，包括主要元素、布局、颜色等。如果是流程图或示意图，请描述其中的流程或结构。
2. **关键词**：提取 3-5 个最能代表图片内容的关键词。
3. **图片类别**：判断图片属于哪种类型：
   - 流程图：展示流程、步骤的图示
   - 示意图：展示结构、原理的图示
   - 实物图：产品、设备的实际照片
   - 界面截图：软件界面的截图
   - 表格图表：数据表格或统计图表
   - 其他：不属于以上类别
4. **文字内容**：如果图片中有文字，请识别并记录所有可见文字。

请严格按照以下 JSON 格式返回结果，不要添加任何其他内容：
{"description": "图片描述内容", "keywords": ["关键词1", "关键词2", "关键词3"], "category": "图片类别", "ocr_text": "识别的文字内容，如无则为空字符串"}"""

        if context:
            base_prompt += f"\n\n以下是图片周围的上下文文本，可以辅助理解图片内容：\n{context}"

        return base_prompt

    def _call_api(self, image_data: str, prompt: str, image_ext: str = "png") -> dict:
        """调用多模态 API（同步模式）"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
            # 注意：不使用 X-DashScope-Async，使用同步模式
        }

        # 构建消息内容
        image_url = f"data:image/{image_ext};base64,{image_data}"

        payload = {
            "model": self.model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"image": image_url},
                            {"text": prompt}
                        ]
                    }
                ]
            },
            "parameters": {
                "max_tokens": 1000,
                "temperature": 0.1  # 低温度以获得更稳定的输出
            }
        }

        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(self.api_url, headers=headers, json=payload)
                response.raise_for_status()
                return response.json()

        except httpx.HTTPStatusError as e:
            print(f"API 请求失败: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            print(f"API 调用异常: {e}")
            raise

    def _parse_response(self, image_id: str, response: dict) -> ImageDescription:
        """解析 API 响应"""
        try:
            # 同步模式响应格式
            content = ""
            if "output" in response:
                output = response["output"]
                # 格式1: choices[0].message.content[0].text
                if "choices" in output:
                    choices = output["choices"]
                    if choices and "message" in choices[0]:
                        msg_content = choices[0]["message"].get("content", [])
                        if msg_content and isinstance(msg_content, list):
                            for item in msg_content:
                                if isinstance(item, dict) and "text" in item:
                                    content = item["text"]
                                    break
                        elif isinstance(msg_content, str):
                            content = msg_content
                # 格式2: results[0].content
                elif "results" in output:
                    results = output["results"]
                    if results:
                        content = results[0].get("content", "")

            if not content:
                content = str(response)

            # 尝试解析 JSON
            data = self._extract_json(content)

            return ImageDescription(
                image_id=image_id,
                description=data.get("description", content),
                keywords=data.get("keywords", []),
                category=data.get("category", "其他"),
                ocr_text=data.get("ocr_text"),
                confidence=0.9
            )

        except Exception as e:
            print(f"解析响应失败: {e}")
            return ImageDescription(
                image_id=image_id,
                description="描述生成失败",
                keywords=[],
                category="其他",
                ocr_text=None,
                confidence=0.0
            )

    def _extract_json(self, content: str) -> dict:
        """从内容中提取 JSON"""
        try:
            # 尝试直接解析
            return json.loads(content)
        except:
            pass

        # 尝试提取 JSON 块
        json_patterns = [
            ('{', '}'),  # 标准 JSON
            ('```json\n', '\n```'),  # Markdown 代码块
            ('```\n', '\n```'),  # 普通代码块
        ]

        for start, end in json_patterns:
            if start in content:
                start_idx = content.find(start)
                end_idx = content.rfind(end)
                if start_idx != -1 and end_idx != -1:
                    json_str = content[start_idx + len(start):end_idx]
                    try:
                        return json.loads(json_str)
                    except:
                        continue

        # 尝试找到第一个 { 和最后一个 }
        json_start = content.find("{")
        json_end = content.rfind("}") + 1
        if json_start != -1 and json_end > json_start:
            try:
                return json.loads(content[json_start:json_end])
            except:
                pass

        return {}

    def _get_cached_description(self, image_path: str) -> Optional[ImageDescription]:
        """获取缓存的图片描述"""
        image_hash = self._compute_image_hash(image_path)
        cache_file = os.path.join(self.cache_dir, f"{image_hash}.json")

        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return ImageDescription(**data)
            except:
                pass
        return None

    def _cache_description(self, image_path: str, description: ImageDescription):
        """缓存图片描述"""
        image_hash = self._compute_image_hash(image_path)
        cache_file = os.path.join(self.cache_dir, f"{image_hash}.json")

        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(asdict(description), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"缓存描述失败: {e}")

    def _compute_image_hash(self, image_path: str) -> str:
        """计算图片文件哈希"""
        with open(image_path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()

    def batch_describe(self, images: List[Dict],
                       max_workers: int = 3,
                       progress_callback=None) -> List[ImageDescription]:
        """
        批量生成图片描述

        Args:
            images: 图片信息列表 [{"image_id": ..., "file_path": ..., "context": ...}]
            max_workers: 并发数（建议不超过 5，避免 API 限流）
            progress_callback: 进度回调函数 callback(current, total, result)

        Returns:
            图片描述列表
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = []
        total = len(images)

        print(f"开始批量生成图片描述，共 {total} 张图片...")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self.describe_image,
                    img["file_path"],
                    img["image_id"],
                    img.get("context", "")
                ): img for img in images
            }

            for i, future in enumerate(as_completed(futures), 1):
                img_info = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    print(f"  [{i}/{total}] 完成: {img_info['image_id']}")

                    if progress_callback:
                        progress_callback(i, total, result)

                except Exception as e:
                    print(f"  [{i}/{total}] 失败: {img_info['image_id']} - {e}")
                    # 创建失败的描述对象
                    results.append(ImageDescription(
                        image_id=img_info["image_id"],
                        description="",
                        keywords=[],
                        category="其他",
                        ocr_text=None,
                        confidence=0.0
                    ))

        success_count = sum(1 for r in results if r.confidence > 0)
        print(f"批量描述完成: {success_count}/{total} 成功")

        return results


def create_image_descriptor(api_key: str, model: str = "qwen-vl-max") -> ImageDescriptor:
    """
    创建图片描述生成器的便捷函数

    Args:
        api_key: DashScope API Key
        model: 模型名称

    Returns:
        ImageDescriptor 实例
    """
    return ImageDescriptor(api_key=api_key, model=model)


if __name__ == "__main__":
    # 测试代码
    import sys

    api_key = os.environ.get("QWEN_API_KEY")
    if not api_key:
        print("请设置 QWEN_API_KEY 环境变量")
        sys.exit(1)

    descriptor = ImageDescriptor(api_key=api_key)

    # 测试单张图片
    test_image = "./extracted_images/test.png"
    if os.path.exists(test_image):
        result = descriptor.describe_image(test_image, "test_001")
        print(f"描述: {result.description}")
        print(f"关键词: {result.keywords}")
        print(f"类别: {result.category}")
