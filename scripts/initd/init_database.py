import sys
import os
import pymysql
from sqlmodel import create_engine, SQLModel
from urllib.parse import urlparse, parse_qs

from app.config import settings

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
# 导入需要创建的数据库表
from app.models.RPA_browser.browser_info_model import *  # type: ignore
from app.models.RPA_browser.plugin_model import *  # type: ignore
from app.models.RPA_browser.notify_model import *  # type: ignore

def create_database():
    """创建数据库"""
    # 解析数据库URL
    parsed_url = urlparse(settings.mysql_browser_info_url)
    
    # 提取连接信息
    host = parsed_url.hostname or '127.0.0.1'
    port = parsed_url.port or 3306
    
    # 从查询参数中提取用户名和密码
    query_params = parse_qs(parsed_url.query)
    username = query_params.get('user', ['root'])[0]
    password = query_params.get('password', [''])[0]
    
    # 从路径中提取数据库名
    database_name = parsed_url.path.lstrip('/')
    if '?' in database_name:
        database_name = database_name.split('?')[0]
    
    print(f"连接信息: host={host}, port={port}, user={username}, database={database_name}")
    
    # 创建不指定数据库的连接
    connection = pymysql.connect(
        host=host,
        port=port,
        user=username,
        password=password,
        charset='utf8mb4'
    )
    
    try:
        with connection.cursor() as cursor:
            # 创建数据库
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            print(f"数据库 '{database_name}' 创建成功或已存在")
    finally:
        connection.close()

def create_tables():
    """创建表"""
    # 先创建数据库
    create_database()
    
    # 创建表的引擎
    engine = create_engine(
        url=settings.mysql_browser_info_url.replace('aiomysql', 'pymysql')  # 这里用的是同步创建数据库表的方法，毕竟只需要运行一次
    )
    SQLModel.metadata.create_all(engine)
    print("所有表创建成功")


if __name__ == '__main__':
    create_tables()