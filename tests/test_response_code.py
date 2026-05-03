"""
测试响应码枚举的使用
"""
from app.models.response import success_response, error_response, custom_response, StandardResponse
from app.models.response_code import ResponseCode


def test_success_response():
    """测试成功响应"""
    response = success_response(data={"key": "value"}, msg="操作成功")
    assert response.code == 0
    assert response.data == {"key": "value"}
    assert response.msg == "操作成功"
    print("✅ success_response 测试通过")


def test_error_response_with_enum():
    """测试使用枚举的错误响应"""
    response = error_response(
        code=ResponseCode.SESSION_NOT_FOUND,
        msg="会话不存在"
    )
    assert response.code == 3001
    assert response.msg == "会话不存在"
    print("✅ error_response with enum 测试通过")


def test_error_response_with_int():
    """测试使用整数的错误响应（向后兼容）"""
    response = error_response(
        code=404,
        msg="资源未找到"
    )
    assert response.code == 404
    assert response.msg == "资源未找到"
    print("✅ error_response with int 测试通过")


def test_custom_response_with_enum():
    """测试使用枚举的自定义响应"""
    response = custom_response(
        code=ResponseCode.WEBRTC_OFFER_FAILED,
        msg="WebRTC offer 创建失败",
        data={"error_detail": "some detail"}
    )
    assert response.code == 2001
    assert response.msg == "WebRTC offer 创建失败"
    assert response.data == {"error_detail": "some detail"}
    print("✅ custom_response with enum 测试通过")


def test_custom_response_with_int():
    """测试使用整数的自定义响应（向后兼容）"""
    response = custom_response(
        code=500,
        msg="服务器错误"
    )
    assert response.code == 500
    assert response.msg == "服务器错误"
    print("✅ custom_response with int 测试通过")


def test_standard_response_direct():
    """测试直接使用 StandardResponse"""
    response = StandardResponse(
        code=ResponseCode.SUCCESS,
        data={"test": "data"},
        msg="测试成功"
    )
    assert response.code == 0
    assert response.data == {"test": "data"}
    assert response.msg == "测试成功"
    print("✅ StandardResponse direct 测试通过")


def test_enum_value_conversion():
    """测试枚举值转换"""
    # 验证枚举可以正确转换为整数
    assert int(ResponseCode.SUCCESS) == 0
    assert int(ResponseCode.NOT_FOUND) == 404
    assert int(ResponseCode.SESSION_NOT_FOUND) == 3001
    assert int(ResponseCode.WEBRTC_OFFER_FAILED) == 2001
    print("✅ Enum value conversion 测试通过")


def test_all_error_codes():
    """测试所有错误码都在正确的范围内"""
    # HTTP 客户端错误 (4xx)
    assert 400 <= ResponseCode.BAD_REQUEST < 500
    assert 400 <= ResponseCode.UNAUTHORIZED < 500
    assert 400 <= ResponseCode.FORBIDDEN < 500
    
    # HTTP 服务器错误 (5xx)
    assert 500 <= ResponseCode.INTERNAL_ERROR < 600
    assert 500 <= ResponseCode.SERVICE_UNAVAILABLE < 600
    
    # 通用业务错误 (1xxx)
    assert 1000 <= ResponseCode.BUSINESS_ERROR < 2000
    assert 1000 <= ResponseCode.VALIDATION_ERROR < 2000
    
    # 浏览器控制错误 (2xxx)
    assert 2000 <= ResponseCode.WEBRTC_OFFER_FAILED < 3000
    assert 2000 <= ResponseCode.SCREENSHOT_FAILED < 3000
    
    # 会话管理错误 (3xxx)
    assert 3000 <= ResponseCode.SESSION_NOT_FOUND < 4000
    assert 3000 <= ResponseCode.HEARTBEAT_FAILED < 4000
    
    # 插件错误 (4xxx)
    assert 4000 <= ResponseCode.PLUGIN_ID_REQUIRED < 5000
    assert 4000 <= ResponseCode.PLUGIN_EXECUTION_FAILED < 5000
    
    # 指纹错误 (5xxx)
    assert 5000 <= ResponseCode.FINGERPRINT_LIMIT_EXCEEDED < 6000
    assert 5000 <= ResponseCode.FINGERPRINT_DELETE_FAILED < 6000
    
    print("✅ All error codes in correct range 测试通过")


if __name__ == "__main__":
    print("开始测试响应码枚举...\n")
    
    test_success_response()
    test_error_response_with_enum()
    test_error_response_with_int()
    test_custom_response_with_enum()
    test_custom_response_with_int()
    test_standard_response_direct()
    test_enum_value_conversion()
    test_all_error_codes()
    
    print("\n🎉 所有测试通过！")
