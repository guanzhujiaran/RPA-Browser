import cv2
import asyncio
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any
import torch
import numpy as np
from io import BytesIO
from PIL import Image
from loguru import logger
import onnxruntime as ort
from modelscope import snapshot_download
from scipy.optimize import linear_sum_assignment
import httpx

from app.utils.decorator import log_class_decorator


@log_class_decorator.decorator
class AsyncCaptchaBreaker:
    """
    异步验证码坐标识别类，使用YOLO11模型进行目标检测
    坐标以左上角为原点，向右为x轴正方向，向下为y轴正方向
    """

    def __init__(self, model_name: str = 'Amorter/CaptchaBreakerModels', device: Optional[str] = None):
        """
        初始化验证码识别器
        
        Args:
            model_name: 模型名称或路径
            device: 设备类型 ('cuda', 'cpu', 'mps')，如果为None则自动选择
        """
        self.model_name = model_name
        self.device = device if device else ('cuda' if torch.cuda.is_available() else 'cpu')
        self._yolo_model = None
        self._siamese_model = None
        self._model_dir = None
        self.logger = logger
        self._load_models()

    def _load_models(self) -> None:
        """加载YOLO和Siamese模型"""
        try:
            # 下载模型
            self._model_dir = snapshot_download(self.model_name)

            # 查找模型文件
            yolo_path = self._find_model_file('yolo')
            siamese_path = self._find_model_file('siamese')

            if not yolo_path or not siamese_path:
                raise FileNotFoundError("未能找到YOLO或Siamese模型文件")

            # 使用ONNX Runtime加载模型
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider'] if self.device == 'cuda' else [
                'CPUExecutionProvider']
            self._yolo_model = ort.InferenceSession(yolo_path, providers=providers)
            self._siamese_model = ort.InferenceSession(siamese_path, providers=providers)

            self.logger.info(f"模型加载成功，设备: {self.device}")

        except Exception as e:
            raise RuntimeError(f"模型加载失败: {str(e)}")

    def _find_model_file(self, model_type: str) -> Optional[str]:
        """
        在下载的模型目录中查找指定类型的模型文件
        
        Args:
            model_type: 模型类型 ('yolo' 或 'siamese')
            
        Returns:
            str: 模型文件路径，如果未找到则返回None
        """
        if not self._model_dir:
            return None

        model_dir_path = Path(self._model_dir)
        patterns = {
            'yolo': ['*yolo*.onnx', '*yolov11*.onnx'],
            'siamese': ['*siamese*.onnx']
        }.get(model_type, [f'*{model_type}*.onnx'])

        # 搜索ONNX模型文件
        for pattern in patterns:
            for file_path in model_dir_path.rglob(pattern):
                if file_path.is_file() and file_path.suffix.lower() == '.onnx':
                    self.logger.info(f"找到{model_type}模型文件: {file_path}")
                    return str(file_path)

        return None

    async def preprocess_image(self, image: np.ndarray) -> np.ndarray:
        """
        异步图像预处理，将图像调整为384x384大小
        
        Args:
            image: 原始图像
            
        Returns:
            np.ndarray: 预处理后的图像
        """
        return await asyncio.to_thread(self._sync_preprocess_image, image)

    def _sync_preprocess_image(self, image: np.ndarray) -> np.ndarray:
        """
        同步图像预处理，将图像调整为384x384大小
        
        Args:
            image: 原始图像
            
        Returns:
            np.ndarray: 预处理后的图像
        """
        height, width = image.shape[:2]
        processed_image = np.zeros((384, 384, 3), dtype=np.uint8)
        processed_image[:height, :width] = image[:height, :width]
        return processed_image

    async def detect_objects(self, image: np.ndarray) -> List[Dict[str, float]]:
        """
        异步使用YOLO11模型进行目标检测
        
        Args:
            image: 预处理后的图像
            
        Returns:
            List[Dict]: 检测到的边界框列表
        """
        return await asyncio.to_thread(self._sync_detect_objects, image)

    def _sync_detect_objects(self, image: np.ndarray) -> List[Dict[str, float]]:
        """
        同步使用YOLO11模型进行目标检测
        
        Args:
            image: 预处理后的图像
            
        Returns:
            List[Dict]: 检测到的边界框列表
        """
        # BGR转RGB并归一化
        input_tensor = np.transpose(cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0, (2, 0, 1))[
            None, ...]

        # ONNX推理
        input_name = self._yolo_model.get_inputs()[0].name
        outputs = self._yolo_model.run(None, {input_name: input_tensor})
        output = outputs[0][0]

        # 过滤并格式化边界框
        bboxes = []
        for row in output:
            if len(row) >= 6 and row[4] > 0.5:  # 置信度阈值
                bboxes.append({
                    'x_min': float(row[0]),
                    'y_min': float(row[1]),
                    'x_max': float(row[2]),
                    'y_max': float(row[3]),
                    'confidence': float(row[4]),
                    'class': int(row[5])
                })

        return bboxes

    def split_boxes(self, bboxes: List[Dict[str, float]]) -> Tuple[List[Dict[str, float]], List[Dict[str, float]]]:
        """
        分离答案框和问题框
        
        Args:
            bboxes: 所有边界框，保持原始顺序
            
        Returns:
            Tuple: (答案框列表, 问题框列表)，均保持原始顺序
        """
        # 按x坐标排序
        sorted_bboxes = sorted(bboxes, key=lambda b: b['x_min'])

        # 分离答案框(y_min < 344)和问题框(y_min >= 344)
        ans_boxes = [b for b in sorted_bboxes if b['y_min'] < 344.0]
        question_boxes = [b for b in sorted_bboxes if b['y_min'] >= 344.0]

        return ans_boxes, question_boxes

    async def crop_and_resize(self, image: np.ndarray, ans_boxes: List[Dict[str, float]],
                              question_boxes: List[Dict[str, float]]) -> np.ndarray:
        """
        异步截取并预处理图像块
        
        Args:
            image: 预处理后的图像
            ans_boxes: 答案框列表
            question_boxes: 问题框列表
            
        Returns:
            np.ndarray: 批量处理后的图像块
        """
        return await asyncio.to_thread(self._sync_crop_and_resize, image, ans_boxes, question_boxes)

    def _sync_crop_and_resize(self, image: np.ndarray, ans_boxes: List[Dict[str, float]],
                              question_boxes: List[Dict[str, float]]) -> np.ndarray:
        """
        同步截取并预处理图像块
        
        Args:
            image: 预处理后的图像
            ans_boxes: 答案框列表（保持原始顺序）
            question_boxes: 问题框列表（保持原始顺序）
            
        Returns:
            np.ndarray: 批量处理后的图像块
        """
        TARGET_SIZE = 96
        all_boxes = ans_boxes + question_boxes
        batch_size = len(all_boxes)
        batch = np.zeros((batch_size, 3, TARGET_SIZE, TARGET_SIZE), dtype=np.float32)

        # 批量处理所有边界框
        for i, bbox in enumerate(all_boxes):
            # 截取图像块
            x_min, y_min = int(bbox['x_min']), int(bbox['y_min'])
            x_max, y_max = int(bbox['x_max']), int(bbox['y_max'])

            # 验证坐标有效性
            if x_max <= x_min or y_max <= y_min:
                continue

            cropped = image[y_min:y_max, x_min:x_max]

            # 跳过空的图像块
            if cropped.size == 0:
                continue

            # 调整大小并归一化
            resized = cv2.resize(cropped, (TARGET_SIZE, TARGET_SIZE),
                                 interpolation=cv2.INTER_LANCZOS4).astype(np.float32) / 255.0

            # 分离BGR通道到批次中
            batch[i, 0, :, :] = resized[:, :, 0]  # B通道
            batch[i, 1, :, :] = resized[:, :, 1]  # G通道
            batch[i, 2, :, :] = resized[:, :, 2]  # R通道

        return batch

    async def extract_features(self, images: np.ndarray) -> np.ndarray:
        """
        异步使用孪生网络提取特征
        
        Args:
            images: 图像批次
            
        Returns:
            np.ndarray: 特征向量
        """
        return await asyncio.to_thread(self._sync_extract_features, images)

    def _sync_extract_features(self, images: np.ndarray) -> np.ndarray:
        """
        同步使用孪生网络提取特征
        
        Args:
            images: 图像批次
            
        Returns:
            np.ndarray: 特征向量
        """
        input_name = self._siamese_model.get_inputs()[0].name
        return self._siamese_model.run(None, {input_name: images})[0]

    def match_features(self, question_features: np.ndarray, ans_features: np.ndarray) -> List[int]:
        """
        构建成本矩阵并使用匈牙利算法计算最优匹配
        
        Args:
            question_features: 问题特征 (按问题框顺序排列)
            ans_features: 答案特征 (按答案框顺序排列)
            
        Returns:
            List[int]: 匹配索引，result[i] 表示第i个问题匹配的答案索引
        """
        # 验证输入特征
        if question_features.size == 0 or ans_features.size == 0:
            return []

        # 构建成本矩阵 - 使用更高效的欧几里得距离计算
        # 使用广播避免显式循环，提高性能
        diff = question_features[:, None, :] - ans_features[None, :, :]
        matrix = np.linalg.norm(diff, axis=2)

        # 匈牙利算法求解最优匹配
        row_indices, col_indices = linear_sum_assignment(matrix)

        # 创建结果数组：对于每个问题，找到对应的答案索引
        result = []
        for i in range(len(question_features)):
            # 找到问题i在row_indices中的位置
            if i in row_indices:
                idx = np.where(row_indices == i)[0][0]
                result.append(col_indices[idx])
            else:
                # 如果没有匹配，使用最近的答案
                result.append(np.argmin(matrix[i]))

        return result

    def generate_results(self, ans_boxes: List[Dict[str, float]], match_indices: List[int]) -> List[
        Tuple[float, float]]:
        """
        生成结果坐标
        
        Args:
            ans_boxes: 答案框列表
            match_indices: 匹配索引，match_indices[i] 表示第i个问题匹配的答案索引
            
        Returns:
            List[Tuple]: 中心点坐标列表
        """
        return [(
            (ans_boxes[idx]['x_min'] + ans_boxes[idx]['x_max']) / 2.0,
            (ans_boxes[idx]['y_min'] + ans_boxes[idx]['y_max']) / 2.0
        ) for idx in match_indices]

    async def predict_chinese_click(self, image: np.ndarray) -> List[Tuple[float, float]]:
        """
        异步预测中文点击验证码的答案坐标
        
        Args:
            image: 图像数组
            
        Returns:
            List[Tuple]: 答案坐标列表，按点击顺序排列
        """
        # 1. 图像预处理
        processed_image = await self.preprocess_image(image)

        # 2. YOLO目标检测
        bboxes = await self.detect_objects(processed_image)

        # 3. 分离答案框和问题框
        ans_boxes, question_boxes = self.split_boxes(bboxes)

        # 4. 截取并预处理图像块
        combined_images = await self.crop_and_resize(processed_image, ans_boxes, question_boxes)

        # 5. 特征提取
        features = await self.extract_features(combined_images)

        # 6. 特征分离和匹配
        ans_count = len(ans_boxes)
        ans_features = features[:ans_count]
        question_features = features[ans_count:]
        match_indices = self.match_features(question_features, ans_features)

        # 7. 生成结果
        return self.generate_results(ans_boxes, match_indices)

    async def predict_chinese_click_from_url(self, url: str) -> List[Tuple[float, float]]:
        """
        从URL异步预测中文点击验证码的答案坐标
        
        Args:
            url: 图像URL
            
        Returns:
            List[Tuple]: 答案坐标列表
        """
        try:
            if not url:
                return []
            # 使用httpx异步发送HTTP请求获取图像
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()

                # 异步处理图像转换
                cv_image = await asyncio.to_thread(self._process_image_data, response.content)

                # 进行预测
                return await self.predict_chinese_click(cv_image)

        except Exception as e:
            raise RuntimeError(f"下载或处理图像失败: {str(e)}")

    def _process_image_data(self, image_data: bytes) -> np.ndarray:
        """
        同步处理图像数据转换为OpenCV格式
        
        Args:
            image_data: 图像字节数据
            
        Returns:
            np.ndarray: OpenCV格式的图像数组
        """
        # 处理图像转换
        pil_image = Image.open(BytesIO(image_data))
        if pil_image.mode == 'RGBA':
            pil_image = pil_image.convert('RGB')

        # 转换为OpenCV格式
        cv_image = np.array(pil_image)
        if len(cv_image.shape) == 3 and cv_image.shape[2] == 3:
            cv_image = cv2.cvtColor(cv_image, cv2.COLOR_RGB2BGR)

        return cv_image

    async def predict_batch_chinese_click(self, urls: List[str]) -> List[List[Tuple[float, float]]]:
        """
        批量异步预测中文点击验证码的答案坐标
        
        Args:
            urls: 图像URL列表
            
        Returns:
            List[List[Tuple]]: 每个URL对应的答案坐标列表
        """

        # 创建多个httpx客户端并发请求
        async def fetch_and_predict(url: str) -> List[Tuple[float, float]]:
            try:
                return await self.predict_chinese_click_from_url(url)
            except Exception as e:
                self.logger.error(f"预测失败 {url}: {str(e)}")
                return []

        # 并发执行所有预测任务
        tasks = [fetch_and_predict(url) for url in urls]
        return await asyncio.gather(*tasks, return_exceptions=False)

    def get_model_info(self) -> Dict[str, Any]:
        """获取模型信息"""
        return {
            'status': '模型已加载' if self._yolo_model and self._siamese_model else '模型未加载',
            'device': self.device,
            'model_dir': self._model_dir
        }

    def visualize_detection_result(self, image: np.ndarray, bboxes: List[Dict[str, float]],
                                   ans_boxes: List[Dict[str, float]], question_boxes: List[Dict[str, float]],
                                   matches: List[int]) -> np.ndarray:
        """
        可视化检测结果，在图像上绘制边界框和顺序编号
        
        Args:
            image: 原始图像
            bboxes: 所有检测到的边界框
            ans_boxes: 答案框列表
            question_boxes: 问题框列表
            matches: 匹配结果，matches[i] 表示第i个问题匹配的答案索引
            
        Returns:
            np.ndarray: 带有标注的结果图像
        """
        # 复制图像以避免修改原始图像
        result_image = image.copy()

        # 绘制所有检测框
        for i, bbox in enumerate(bboxes):
            x_min = int(bbox['x_min'])
            y_min = int(bbox['y_min'])
            x_max = int(bbox['x_max'])
            y_max = int(bbox['y_max'])

            # 使用不同颜色区分答案框和问题框
            if bbox in ans_boxes:
                color = (0, 255, 0)  # 绿色表示答案框
                box_type = "A"
            else:
                color = (255, 0, 0)  # 蓝色表示问题框
                box_type = "Q"

            # 绘制边界框
            cv2.rectangle(result_image, (x_min, y_min), (x_max, y_max), color, 2)

            # 添加置信度和类型标签
            conf = bbox['confidence']
            label = f"{box_type}_{i}: {conf:.2f}"
            cv2.putText(result_image, label, (x_min, y_min - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

            center_x = (bbox['x_min'] + bbox['x_max']) / 2
            center_y = (bbox['y_min'] + bbox['y_max']) / 2

        # 标注匹配结果
        for i, match_idx in enumerate(matches):
            # 检查索引是否有效
            if 0 <= match_idx < len(ans_boxes):
                # 获取匹配的问题框和答案框
                if i < len(question_boxes):
                    question_box = question_boxes[i]
                    # 计算问题框中心点
                    question_center_x = int((question_box['x_min'] + question_box['x_max']) / 2)
                    question_center_y = int((question_box['y_min'] + question_box['y_max']) / 2)
                else:
                    question_box = None
                    question_center_x = question_center_y = None

                ans_box = ans_boxes[match_idx]

                # 计算答案框中心点
                ans_center_x = int((ans_box['x_min'] + ans_box['x_max']) / 2)
                ans_center_y = int((ans_box['y_min'] + ans_box['y_max']) / 2)

                # 绘制更小更明显的数字标记表示点击顺序
                label_number = i + 1
                cv2.circle(result_image, (ans_center_x, ans_center_y), 12, (0, 0, 255), -1)  # 实心红圆
                cv2.circle(result_image, (ans_center_x, ans_center_y), 12, (255, 255, 255), 1)  # 白色边框
                # 添加白色数字，使其更明显
                cv2.putText(result_image, str(label_number), (ans_center_x - 5, ans_center_y + 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)  # 白色数字

                # 如果有问题框，绘制连接线
                if question_box and question_center_x is not None and question_center_y is not None:
                    # 绘制连线
                    cv2.line(result_image, (question_center_x, question_center_y),
                             (ans_center_x, ans_center_y), (255, 255, 0), 2)

        return result_image

    async def predict_chinese_click_with_details(self, image: np.ndarray) -> Dict[str, Any]:
        """
        异步预测中文点击验证码的答案坐标，并返回详细信息用于调试
        
        Args:
            image: 图像数组
            
        Returns:
            Dict: 包含坐标、边界框和匹配详情的结果
        """
        # 1. 图像预处理
        processed_image = await self.preprocess_image(image)

        # 2. YOLO目标检测
        bboxes = await self.detect_objects(processed_image)

        # 3. 分离答案框和问题框
        ans_boxes, question_boxes = self.split_boxes(bboxes)

        # 4. 截取并预处理图像块
        combined_images = await self.crop_and_resize(processed_image, ans_boxes, question_boxes)

        # 5. 特征提取
        features = await self.extract_features(combined_images)

        # 6. 特征分离和匹配
        ans_count = len(ans_boxes)
        ans_features = features[:ans_count]
        question_features = features[ans_count:]

        # 构建成本矩阵
        diff = question_features[:, None, :] - ans_features[None, :, :]
        cost_matrix = np.linalg.norm(diff, axis=2)

        # 使用匈牙利算法计算最优匹配
        row_indices, col_indices = linear_sum_assignment(cost_matrix)

        # 创建结果数组：对于每个问题，找到对应的答案索引
        match_indices = []
        for i in range(len(question_boxes)):
            # 找到问题i在row_indices中的位置
            if i in row_indices:
                idx = np.where(row_indices == i)[0][0]
                match_indices.append(col_indices[idx])
            else:
                # 如果没有匹配，使用最近的答案
                match_indices.append(np.argmin(cost_matrix[i]))

        # 验证匹配结果
        for i, ans_idx in enumerate(match_indices):
            if i < len(question_boxes) and ans_idx < len(ans_boxes):
                q_box = question_boxes[i]
                a_box = ans_boxes[ans_idx]
                q_center = ((q_box['x_min'] + q_box['x_max']) / 2, (q_box['y_min'] + q_box['y_max']) / 2)
                a_center = ((a_box['x_min'] + a_box['x_max']) / 2, (a_box['y_min'] + a_box['y_max']) / 2)
                distance = cost_matrix[i, ans_idx]

        # 7. 生成结果
        result_coords = self.generate_results(ans_boxes, match_indices)

        return {
            'coordinates': result_coords,
            'all_bboxes': bboxes,
            'answer_boxes': ans_boxes,
            'question_boxes': question_boxes,
            'matches': match_indices,
            'cost_matrix': cost_matrix
        }


# 创建实例并导出
acb = AsyncCaptchaBreaker()

__all__ = ['acb']  # 导出单例实例

if __name__ == '__main__':
    # 示例用法
    async def main():
        # 测试URL列表
        test_urls = [
            'https://static.geetest.com/captcha_v3/batch/v3/123719/2025-12-04T20/word/3c69c439c3e248e5a54b70ae88bc4acb.jpg?challenge=e81f8a273667c1f710ed7133c121e0cd',
            'https://static.geetest.com/captcha_v3/batch/v3/123775/2025-12-05T10/word/6bea08645c644b7abed0f4085142b422.jpg?challenge=124fefb0f28fb1caed10f4caf161a233',
            'https://static.geetest.com/captcha_v3/batch/v3/123775/2025-12-05T10/word/3ca8a0da9b28432c984b4ab2ee68af42.jpg?challenge=1b23797880523b7bdb6afd0635fa2f7b',
            'https://static.geetest.com/captcha_v3/batch/v3/123791/2025-12-05T14/word/e2af1ee2b48d4f45bb92d5fac183be37.jpg?challenge=a473950c2a09f7c152f7d4eadfe536ff',
            'https://static.geetest.com/captcha_v3/batch/v3/123795/2025-12-05T15/word/3589c76901fd461a9364d9ef6354a77f.jpg?challenge=a473950c2a09f7c152f7d4eadfe536ff',
            'https://static.geetest.com/captcha_v3/batch/v3/123795/2025-12-05T15/word/4bcf9479d747449cab2e15e9d64270cd.jpg?challenge=a473950c2a09f7c152f7d4eadfe536ff',
            'https://static.geetest.com/captcha_v3/batch/v3/123795/2025-12-05T15/word/6724642b9e98466580bf37990a4ae840.jpg?challenge=a473950c2a09f7c152f7d4eadfe536ff',
        ]

        breaker = AsyncCaptchaBreaker()

        for i, url in enumerate(test_urls):
            try:
                # 获取详细预测结果
                async with httpx.AsyncClient() as client:
                    response = await client.get(url)
                    image_data = await asyncio.to_thread(breaker._process_image_data, response.content)
                result = await breaker.predict_chinese_click_with_details(image_data)

                # 可视化结果
                result_image = breaker.visualize_detection_result(
                    image_data,
                    result['all_bboxes'],
                    result['answer_boxes'],
                    result['question_boxes'],
                    result['matches']
                )

                # 显示图像（如果在支持GUI的环境中）
                try:
                    cv2.imshow('Captcha Detection Result', result_image)
                    cv2.waitKey(0)
                    cv2.destroyAllWindows()
                except:
                    print("无法显示图像，请确保在支持GUI的环境中运行")

                # 保存结果图像到文件
                output_file = f'captcha_result_{i + 1}.jpg'
                cv2.imwrite(output_file, result_image)
                print(f"结果图像已保存为: {output_file}")

            except Exception as e:
                print(f"测试 {i + 1} 失败: {str(e)}")
                import traceback
                traceback.print_exc()

        try:
            # 批量预测验证码答案（并发执行）
            batch_results = await breaker.predict_batch_chinese_click(test_urls[:3])
            for i, coordinates in enumerate(batch_results):
                print(f"批量测试 {i + 1}: 预测坐标 {coordinates}")
        except Exception as e:
            print(f"批量预测失败: {str(e)}")


    asyncio.run(main())
