import sys
import os
from sqlmodel import create_engine
from sqlalchemy import text

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app.config import settings

def verify_tables():
    """验证表是否创建成功"""
    # 创建引擎
    engine = create_engine(settings.mysql_browser_info_url.replace('aiomysql', 'pymysql'))
    
    # 查询所有表
    with engine.connect() as conn:
        result = conn.execute(text('SHOW TABLES'))
        tables = [row[0] for row in result]
        print("数据库中的表:")
        for table in tables:
            print(f"  - {table}")
    
    print(f"\n总共找到 {len(tables)} 个表")

if __name__ == '__main__':
    verify_tables()