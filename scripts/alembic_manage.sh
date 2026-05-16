#!/bin/bash
# Alembic 迁移管理脚本

set -e

# 项目根目录
PROJECT_ROOT="/home/minato_aqua/BilibiliExplosion/RPA-Browser"
cd "$PROJECT_ROOT"

# 设置 Python 路径
export PYTHONPATH="$PROJECT_ROOT:$PYTHONPATH"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 打印帮助信息
print_help() {
    echo -e "${GREEN}Alembic 数据库迁移管理工具${NC}"
    echo ""
    echo "用法: ./scripts/alembic_manage.sh <command> [options]"
    echo ""
    echo "命令:"
    echo "  generate <message>    生成新的迁移（需要提供描述信息）"
    echo "  upgrade               升级到最新版本"
    echo "  downgrade             回滚一个版本"
    echo "  current               显示当前版本"
    echo "  history               显示迁移历史"
    echo "  check                 检查是否有未应用的迁移"
    echo "  backup                备份数据库"
    echo "  help                  显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  ./scripts/alembic_manage.sh generate \"Add user email field\""
    echo "  ./scripts/alembic_manage.sh upgrade"
    echo "  ./scripts/alembic_manage.sh backup"
}

# 生成迁移
generate_migration() {
    if [ -z "$1" ]; then
        echo -e "${RED}错误: 请提供迁移描述信息${NC}"
        echo "用法: ./scripts/alembic_manage.sh generate \"描述信息\""
        exit 1
    fi
    
    echo -e "${YELLOW}正在生成迁移: $1${NC}"
    alembic revision --autogenerate -m "$1"
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ 迁移生成成功${NC}"
        echo -e "${YELLOW}提示: 请检查生成的迁移文件，确认无误后执行 'upgrade' 命令应用迁移${NC}"
    else
        echo -e "${RED}✗ 迁移生成失败${NC}"
        exit 1
    fi
}

# 升级数据库
upgrade_db() {
    echo -e "${YELLOW}正在升级数据库到最新版本...${NC}"
    alembic upgrade head
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ 数据库升级成功${NC}"
    else
        echo -e "${RED}✗ 数据库升级失败${NC}"
        exit 1
    fi
}

# 降级数据库
downgrade_db() {
    echo -e "${YELLOW}警告: 即将回滚一个版本，确定要继续吗？(y/n)${NC}"
    read -r response
    if [[ "$response" =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}正在回滚数据库...${NC}"
        alembic downgrade -1
        
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✓ 数据库回滚成功${NC}"
        else
            echo -e "${RED}✗ 数据库回滚失败${NC}"
            exit 1
        fi
    else
        echo -e "${YELLOW}已取消操作${NC}"
    fi
}

# 显示当前版本
show_current() {
    echo -e "${YELLOW}当前数据库版本:${NC}"
    alembic current
}

# 显示迁移历史
show_history() {
    echo -e "${YELLOW}迁移历史:${NC}"
    alembic history --verbose
}

# 检查迁移状态
check_status() {
    echo -e "${YELLOW}检查迁移状态...${NC}"
    alembic check
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ 数据库与模型同步${NC}"
    else
        echo -e "${YELLOW}⚠ 检测到未同步的更改，建议生成新的迁移${NC}"
    fi
}

# 备份数据库
backup_db() {
    BACKUP_DIR="$PROJECT_ROOT/backups"
    mkdir -p "$BACKUP_DIR"
    
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    BACKUP_FILE="$BACKUP_DIR/bilirpabackup_$TIMESTAMP.sql"
    
    echo -e "${YELLOW}正在备份数据库...${NC}"
    echo -e "${YELLOW}备份文件: $BACKUP_FILE${NC}"
    
    # 从配置中解析数据库连接信息
    DB_URL=$(python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from app.config import settings
url = settings.mysql_browser_info_url
if url.startswith('mysql+aiomysql://'):
    url = url.replace('mysql+aiomysql://', 'mysql://')
print(url)
")
    
    # 解析 URL
    DB_USER=$(echo "$DB_URL" | grep -oP '://\K[^:]+')
    DB_PASS=$(echo "$DB_URL" | grep -oP ':[^@]+@' | tr -d ':@')
    DB_HOST=$(echo "$DB_URL" | grep -oP '@\K[^:/]+')
    DB_PORT=$(echo "$DB_URL" | grep -oP ':\K\d+' | head -1)
    DB_NAME=$(echo "$DB_URL" | grep -oP '/\K[^?]+')
    
    mysqldump -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" > "$BACKUP_FILE"
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ 数据库备份成功${NC}"
        echo -e "${YELLOW}备份文件大小: $(du -h "$BACKUP_FILE" | cut -f1)${NC}"
    else
        echo -e "${RED}✗ 数据库备份失败${NC}"
        exit 1
    fi
}

# 主逻辑
case "$1" in
    generate)
        generate_migration "$2"
        ;;
    upgrade)
        upgrade_db
        ;;
    downgrade)
        downgrade_db
        ;;
    current)
        show_current
        ;;
    history)
        show_history
        ;;
    check)
        check_status
        ;;
    backup)
        backup_db
        ;;
    help|--help|-h)
        print_help
        ;;
    *)
        echo -e "${RED}错误: 未知命令 '$1'${NC}"
        echo ""
        print_help
        exit 1
        ;;
esac
