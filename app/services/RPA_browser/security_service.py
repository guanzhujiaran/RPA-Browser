"""
JavaScript代码安全检查服务

提供JavaScript代码的安全分析和风险评估功能，确保代码在浏览器中安全执行。
"""

import re
import time
from typing import List, Tuple
from app.models.RPA_browser.live_control_models import JavaScriptExecutionResult
from app.models.RPA_browser.rpa_operation_models import (
    SecurityCheckParams, 
    SecurityCheckResult, 
    SecurityRisk
)


class SecurityChecker:
    """JavaScript代码安全检查器"""
    
    # 危险模式列表
    DANGEROUS_PATTERNS = {
        # 文件系统操作
        'fs_access': {
            'patterns': [r'\bfs\.', r'\brequire\s*\(\s*["\']fs', r'\bimport\s+.*fs'],
            'level': 'high',
            'description': '文件系统访问'
        },
        # 网络请求
        'network_request': {
            'patterns': [r'\bfetch\s*\(', r'\bXMLHttpRequest', r'\baxios\.', r'\.get\s*\(', r'\.post\s*\('],
            'level': 'high',
            'description': '网络请求'
        },
        # 进程执行
        'process_execution': {
            'patterns': [r'\bchild_process', r'\bexec\s*\(', r'\bspawn\s*\(', r'\beval\s*\(', r'\bFunction\s*\('],
            'level': 'high',
            'description': '进程执行或动态代码执行'
        },
        # 系统信息
        'system_info': {
            'patterns': [r'\bos\.', r'\bprocess\.', r'\bnavigator\.', r'\bwindow\.location'],
            'level': 'medium',
            'description': '系统信息获取'
        },
        # DOM操作潜在风险
        'dom_manipulation': {
            'patterns': [r'\binnerHTML\s*=', r'\bouterHTML\s*=', r'\bdocument\.write'],
            'level': 'medium',
            'description': 'DOM内容修改'
        },
        # 无限循环检测
        'infinite_loop': {
            'patterns': [r'while\s*\(\s*true\s*\)', r'for\s*\(\s*;;\s*\)', r'while\s*\(\s*1\s*\)'],
            'level': 'high',
            'description': '潜在的无限循环'
        },
        # 危险全局变量修改
        'global_modification': {
            'patterns': [r'window\.\w+\s*=', r'global\.\w+\s*=', r'\bObject\.defineProperty'],
            'level': 'medium',
            'description': '全局变量修改'
        }
    }
    
    # 允许的操作模式
    ALLOWED_PATTERNS = {
        'dom_selection': [r'\bquerySelector', r'\bgetElementById', r'\bgetElementsBy', r'\bquerySelectorAll'],
        'dom_manipulation_safe': [r'\bclick\s*\(', r'\bfocus\s*\(', r'\bblur\s*\(', r'\bscroll', r'\bscrollTo'],
        'input_simulation': [r'\bvalue\s*=', r'\bdispatchEvent', r'\bcreateEvent'],
        'data_processing': [r'\bconsole\.', r'\bMath\.', r'\bArray\.', r'\bString\.', r'\bObject\.'],
        'timing': [r'\bsetTimeout', r'\bsetInterval', r'\bPromise', r'\basync', r'\bawait'],
        'page_navigation': [r'\blocation\.href', r'\bhistory\.', r'\bwindow\.open']
    }
    
    @staticmethod
    def check_code_security(params: SecurityCheckParams) -> SecurityCheckResult:
        """
        检查JavaScript代码的安全性
        
        Args:
            params: 安全检查参数
            
        Returns:
            SecurityCheckResult: 检查结果
        """
        code = params.code
        
        risks = []
        allowed_ops = []
        blocked_ops = []
        
        # 按行分割代码进行行级检查
        lines = code.split('\n')
        
        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith('//') or line.startswith('/*'):
                continue
                
            # 检查危险模式
            for risk_type, risk_info in SecurityChecker.DANGEROUS_PATTERNS.items():
                for pattern in risk_info['patterns']:
                    if re.search(pattern, line, re.IGNORECASE):
                        risk = SecurityRisk(
                            type=risk_type,
                            level=risk_info['level'],
                            description=risk_info['description'],
                            line=line_num,
                            pattern=pattern
                        )
                        risks.append(risk)
                        blocked_ops.append(risk_info['description'])
            
            # 检查允许的操作
            for op_type, patterns in SecurityChecker.ALLOWED_PATTERNS.items():
                for pattern in patterns:
                    if re.search(pattern, line, re.IGNORECASE):
                        allowed_ops.append(op_type)
                        break
        
        # 去重
        allowed_ops = list(set(allowed_ops))
        blocked_ops = list(set(blocked_ops))
        
        # 计算风险等级和评分
        risk_level, score = SecurityChecker._calculate_risk_level(risks, params.strict_mode)
        
        # 生成安全建议
        recommendations = SecurityChecker._generate_recommendations(risks)
        
        # 判断是否安全可执行
        safe_to_execute = (
            risk_level == 'low' or 
            (not params.strict_mode and risk_level == 'medium' and score >= 60)
        )
        
        return SecurityCheckResult(
            level=risk_level,
            score=score,
            risks=risks,
            allowed_operations=allowed_ops,
            blocked_operations=blocked_ops,
            safe_to_execute=safe_to_execute,
            recommendations=recommendations
        )
    
    @staticmethod
    def _calculate_risk_level(risks: List[SecurityRisk], strict_mode: bool) -> Tuple[str, int]:
        """
        计算风险等级和评分
        
        Args:
            risks: 风险列表
            strict_mode: 是否严格模式
            
        Returns:
            Tuple[str, int]: (风险等级, 评分)
        """
        if not risks:
            return "low", 100
            
        risk_scores = {'low': 10, 'medium': 30, 'high': 60}
        total_score = 100
        
        for risk in risks:
            total_score -= risk_scores.get(risk.level, 30)
            
        # 根据评分确定风险等级
        if total_score >= 80:
            level = "low"
        elif total_score >= 50:
            level = "medium"
        else:
            level = "high"
            
        # 严格模式下更严格
        if strict_mode and total_score < 90:
            level = "medium" if total_score >= 70 else "high"
            
        return level, max(0, min(100, total_score))
    
    @staticmethod
    def _generate_recommendations(risks: List[SecurityRisk]) -> List[str]:
        """
        生成安全建议
        
        Args:
            risks: 风险列表
            
        Returns:
            List[str]: 建议列表
        """
        recommendations = []
        
        if not risks:
            recommendations.append("代码通过安全检查，可以安全执行")
            return recommendations
            
        # 按风险类型统计
        risk_types = {}
        for risk in risks:
            if risk.type not in risk_types:
                risk_types[risk.type] = []
            risk_types[risk.type].append(risk)
        
        # 针对不同风险类型生成建议
        for risk_type, risk_list in risk_types.items():
            if risk_type == 'fs_access':
                recommendations.append("避免文件系统操作，如需数据处理请使用浏览器内置API")
            elif risk_type == 'network_request':
                recommendations.append("避免直接网络请求，使用页面已有的接口或DOM操作")
            elif risk_type == 'process_execution':
                recommendations.append("禁止使用eval、Function构造函数等动态执行功能")
            elif risk_type == 'system_info':
                recommendations.append("限制系统信息获取，专注页面操作")
            elif risk_type == 'dom_manipulation':
                recommendations.append("谨慎使用innerHTML，优先使用textContent或其他安全方法")
            elif risk_type == 'infinite_loop':
                recommendations.append("避免无限循环，确保有明确的退出条件")
            elif risk_type == 'global_modification':
                recommendations.append("避免修改全局变量，使用局部作用域")
        
        # 通用建议
        recommendations.extend([
            "建议在安全沙箱环境中执行代码",
            "设置合理的执行超时时间",
            "监控代码执行过程中的资源使用情况"
        ])
        
        return recommendations
    
    @staticmethod
    def sanitize_code(code: str) -> str:
        """
        净化代码，移除或替换危险操作
        
        Args:
            code: 原始代码
            
        Returns:
            str: 净化后的代码
        """
        # 这里可以实现代码净化逻辑
        # 例如替换危险的API调用等
        sanitized = code
        
        # 移除明显的危险调用
        dangerous_calls = ['eval(', 'Function(', 'require(']
        for call in dangerous_calls:
            sanitized = re.sub(r'\b' + re.escape(call), r'/* BLOCKED */ ' + call, sanitized)
            
        return sanitized


class JavaScriptSandbox:
    """JavaScript代码执行沙箱"""
    
    @staticmethod
    async def execute_with_safety(
        page, 
        code: str, 
        timeout: int = 30000,
        safety_check: bool = True
    ) -> JavaScriptExecutionResult:
        """
        在沙箱环境中安全执行JavaScript代码
        
        Args:
            page: Playwright页面对象
            code: 要执行的JavaScript代码
            timeout: 执行超时时间
            safety_check: 是否进行安全检查
            
        Returns:
            JavaScriptExecutionResult: 执行结果
        """
        start_time = time.time()
        
        if safety_check:
            # 执行安全检查
            check_params = SecurityCheckParams(code=code)
            check_result = SecurityChecker.check_code_security(check_params)
            
            if not check_result.safe_to_execute:
                return JavaScriptExecutionResult(
                    success=False,
                    error=f'代码安全检查失败: {check_result.level} 风险等级',
                    execution_time=int((time.time() - start_time) * 1000),
                    risks=[risk.dict() for risk in check_result.risks]
                )
        
        try:
            # 在浏览器中执行代码，包装在try-catch中
            wrapped_code = f'''
            (async function() {{
                try {{
                    {code}
                }} catch (error) {{
                    return {{
                        success: false,
                        error: error.toString(),
                        stack: error.stack
                    }};
                }}
            }})()
            '''
            
            # 设置页面默认超时时间
            original_timeout = page.timeout
            page.set_default_timeout(timeout)
            
            try:
                js_result = await page.evaluate(wrapped_code)
            finally:
                # 恢复原始超时设置
                page.set_default_timeout(original_timeout)
            
            execution_time = int((time.time() - start_time) * 1000)
            
            return JavaScriptExecutionResult(
                success=True,
                result=js_result,
                execution_time=execution_time
            )
            
        except Exception as e:
            execution_time = int((time.time() - start_time) * 1000)
            return JavaScriptExecutionResult(
                success=False,
                error=f'执行失败: {str(e)}',
                execution_time=execution_time
            )