import os

from loguru import logger
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 「推送 / 服务标识」配置片段与推送渠道配置模型统一来自 bili-common（单一来源）
from bili_common.core.push_settings import PushNotifySettingsMixin
# 兼容 re-export：存量 `app.config.PushChannelConfig` 的引用无需改动
from bili_common.models.push import PushChannelConfig

from app.models.consts.enums import ConfigRunningModeEnum

current_dir = os.path.dirname(__file__)
# 项目根目录（RPA-Browser/），所有基于项目根的运行时路径都应从这里派生，
# 不要再使用 Path(__file__).parent.parent... 层层回溯
PROJECT_ROOT = os.path.abspath(os.path.join(current_dir, ".."))


class Settings(PushNotifySettingsMixin, BaseSettings):
    mysql_browser_info_url: str
    RUNNING_MODE: ConfigRunningModeEnum
    controller_base_path: str | None = "/api"
    chromium_executable_dir: str | None = os.path.join(current_dir, "chrome")
    # 有头(headful)模式是反自动化检测的关键，但服务器没有物理显示器，
    # 因此用 Xvfb 提供虚拟屏幕：浏览器仍是完整有头模式，只是不真的显示出来
    xvfb_enabled: bool = True
    xvfb_display: str = ":99"
    xvfb_screen: str = "1920x1080x24"
    jwt_algorithm: str = "HS256"  # JWT算法
    jwt_expire_minutes: int = 7 * 24 * 60  # JWT过期时间（分钟），默认30分钟
    proxy_server_url: str = "http://127.0.0.1:10809"  # 可以访问外网的代理地址
    github_proxy_urls: list[str | None] = Field(
        default_factory=lambda: [
            None,
            "https://gh-proxy.com/",
            "https://gh-proxy.org/",
            "https://ghproxy.net/",
            "https://gh.b52m.cn/",
            "https://github.xxlab.tech/",
            "https://ghproxy.053000.xyz/",
            "https://proxy.yaoyaoling.net/",
            "https://gh.aaa.team/",
            "https://g.blfrp.cn/",
            "https://github.chenc.dev/",
            "https://github.dpik.top/",
            "https://gh-proxy.com/",
            "https://github.cnxiaobai.com/",
            "https://gh.padao.fun/",
            "https://ghproxy.sakuramoe.dev/",
            "https://30006000.xyz/",
            "https://gh.monlor.com/",
            "https://ghp.keleyaa.com/",
            "https://ghproxy.mirror.skybyte.me/",
            "https://fastgit.cc/",
            "https://xiaomo-station.top/",
            "https://github-proxy.lixxing.top/",
            "https://gh.shiina-rimo.cafe/",
            "https://gh.idayer.com/",
            "https://gh.996986.xyz/",
            "https://gitproxy.mrhjx.cn/",
            "https://getgit.love8yun.eu.org/",
            "https://ghm.078465.xyz/",
            "https://gh.ddlc.top/",
            "https://git.yylx.win/",
            "https://gh.198962.xyz/",
            "https://proxy.baguoyuyan.com/",
            "https://ghproxy.imciel.com/",
            "https://jiashu.1win.eu.org/",
            "https://git.820828.xyz/",
            "https://gh.1k.ink/",
            "https://ghproxy.net/",
            "https://github.ihnic.com/",
            "https://ghpxy.hwinzniej.top/",
            "https://github.mlmle.cn/",
            "https://gp.871201.xyz/",
            "https://github.zzrbk.xyz/",
            "https://ghproxy.cxkpro.top/",
            "https://gh.catmak.name/",
            "https://ghproxy.xzhouqd.com/",
            "https://hub.ddayh.com/",
            "https://kenyu.ggff.net/",
            "https://gh.halonice.com/",
            "https://gh.nxnow.top/",
            "https://github.boringhex.top/",
            "https://github.crdz.eu.org/",
            "https://github.lsdfxdk.nyc.mn/",
            "https://github.ednovas.xyz/",
            "https://tvv.tw/",
            "https://ggg.clwap.dpdns.org/",
            "https://github.788787.xyz/",
            "https://github.tianrld.top/",
            "https://gh.chjina.com/",
            "https://github.1ms.xx.kg/",
            "https://git.951959483.xyz/",
            "https://github.880824.xyz/",
            "https://gh.chalin.tk/",
            "https://gh.noki.icu/",
            "https://www.5555.cab/",
            "https://ghf.无名氏.top/",
            "https://y.whereisdoge.work/",
            "https://gh.xxooo.cf/",
            "https://github-proxy.memory-echoes.cn/",
            "https://free.cn.eu.org/",
            "https://github.geekery.cn/",
            "https://ghps.cc/",
            "https://gitproxy.127731.xyz/",
            "https://gh.con.sh/",
            "https://gh.dpik.top/",
            "https://down.npee.cn/",
            "https://git.669966.xyz/",
            "https://ghfile.geekertao.top/",
            "https://ghproxy.cn/",
            "https://git.40609891.xyz/",
            "https://ghproxy.monkeyray.net/",
            "https://gitproxy1.127731.xyz/",
            "https://hub.gitmirror.com/",
            "https://ghproxy.xiaopa.cc/",
            "https://ghproxy.cfd/",
            "https://github.tbedu.top/",
            "https://ghproxy.vansour.top/",
            "https://gh.wsmdn.dpdns.org/",
            "https://gh.bugdey.us.kg/",
            "https://github.bullb.net/",
            "https://github.ruojian.space/",
            "https://code-hub-hk.freexy.top/",
            "https://gitproxy.197545.xyz/",
            "https://ghproxy.mf-dust.dpdns.org/",
            "https://gh.jasonzeng.dev/",
            "https://j.1lin.dpdns.org/",
            "https://j.1win.ggff.net/",
            "https://git.zeas.cc/",
            "https://gh.echofree.xyz/",
            "https://github.kkproxy.dpdns.org/",
            "https://ghb.nilive.top/",
            "https://github.cn86.dev/",
            "https://github.oterea.top/",
            "https://ghproxy.fangkuai.fun/",
            "https://gh-proxy.net/",
            "https://gitproxy.click/",
            "https://ghproxy.cc/",
            "https://cf.ghproxy.cc/",
            "https://proxy.atoposs.com/",
            "https://github-proxy.com/",
            "https://github.zjzzy.cloudns.org/",
            "https://ghfast.top/",
            "https://gp.zkitefly.eu.org/",
            "https://gh.jdck.fun/",
            "https://git.tangbai.cc/",
            "https://ghproxy.1888866.xyz/",
            "https://github.limoruirui.com/",
            "https://gh.llkk.cc/",
            "https://gh.39.al/",
        ]
    )

    # RabbitMQ 连接地址（rabbitmq_url）由 PushNotifySettingsMixin 提供，默认即
    # docker-compose 内部服务名；本服务经它做 RPC 调用（不经过网关、不依赖 JWT），
    # 后端定时执行工作流也走同一连接串。

    # be-message 服务地址：管理员身份判定（GET /api/v1/message/admin/me）统一由
    # be-message 裁决（权限数据存于其 msg_admin 表），RPA 不再持有独立 RpaAdmin 表。
    # 默认指向 docker-compose 内部服务名；本地开发在 .env.dev 覆盖为 127.0.0.1。
    message_service_url: str = "http://be-message-service:18739"

    admin_base_path: str = "/api/admin/rpa"

    # 是否强制要求执行操作（execute，如执行工作流/定时计划）前已通过对应审批单
    # 与 publish 分开管控：默认关闭，治理成熟后开启，避免连带阻断社区发布
    require_approval_enabled: bool = False

    # 是否强制要求「公开到社区」的发布动作已通过 publish 审批单
    # （未通过则资源保持 is_public=false，一直 private）
    require_publish_approval_enabled: bool = True

    model_config = SettingsConfigDict(
        env_file=(
            os.path.join(current_dir, "../.env.prod"),
            os.path.join(current_dir, "../.env.dev"),
        ),
        case_sensitive=False,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 底下是不那么重要的配置
    # 推送渠道配置（message_config）、服务标识（SERVER_NAME / SERVER_ADDRESS）、
    # 渠道默认端点（pushme_url / pushplus_url）、hitokoto_api_url 等共用项
    # 由 PushNotifySettingsMixin 提供，此处只覆盖本服务有差异的默认值。
    SERVER_NAME: str = "rpa-browser"
    GEMINI_API_KEY: str = "NotNecessary"
    default_proxy_server: str = (
        ""  # 只要ip加端口就行,别加协议,httpx的all会自动处理,类似127.0.0.1:3128
    )

    # ==================== browser_id（短雪花 ID，分钟步进）====================
    # 对外发布 ID 统一收敛到 bili_common 的分钟级短雪花（见规则 snowflake-id.mdc）：
    # 总位数恒 39 bits，初始 7~8 位十进制，取代原先第三方 snowflake-id 包的
    # 毫秒级 63 bits（18~19 位）实现。
    #
    # browser_id epoch（秒级时间戳）：默认 2026-01-01 00:00:00 UTC+8，
    # 可通过环境变量 BROWSER_ID_EPOCH_SEC 覆盖
    browser_id_epoch_sec: int = 1767196800
    # browser_id worker 编号（0~15），通过环境变量 BROWSER_ID_WORKER_ID 设置。
    # 多实例部署时必须互不相同；且需与 uid(1)/moment_id(2)/topic_id(3) 错开，
    # 避免跨实体在共用列（如 be-message 的 bizId）上碰撞
    browser_id_worker_id: int = 4
    # browser_id 序列号位宽（默认 4 = 每 worker 每分钟最多 16 个，总位数恒 39 bits）。
    # 开发/测试环境可放宽（如 7 = 每分钟 128 个）；⚠️ 变更位宽会改变对外 ID 数值
    # 空间、与已发布 ID 可能重叠，仅限清库重建的环境启用，生产保持默认 4。
    browser_id_sequence_bits: int = 4

    # 浏览器会话默认配置
    browser_session_auto_cleanup: bool = True  # 是否启用自动清理
    browser_session_max_idle_time: int = 1800  # 最大闲置时间（秒）→ 超过后关实例出池
    browser_session_cleanup_interval: int = 60  # 清理检查间隔（秒）
    browser_session_expiration_time: int | None = (
        None  # 会话过期时间（秒），None表示不过期
    )
    # ── 会话闲置三级软着陆（详见 docs/be-message-统一计划书.md §5.15）──
    # 活跃时间戳统一由 LiveService.touch() 刷新；三级阈值须满足
    # degrade_after < suspend_after < max_idle_time
    browser_stream_degrade_after: int = 120  # 闲置降级阈值（秒）：降质降帧
    browser_stream_suspend_after: int = 300  # 闲置挂起阈值（秒）：关流保实例
    browser_session_terminate_grace: int = 60  # 关实例前宽限倒计时（秒）

    # ── 浏览器启动内存准入与排队 ──
    # 启动浏览器前先检查系统可用内存：内存充足立即放行；不足则进入启动队列排队。
    # 队列分两条：VIP 队列优先于普通用户队列；VIP 身份来自 x-bili-vip-status 请求头。
    browser_launch_queue_enabled: bool = True  # 是否启用内存准入排队（关闭=不做任何限制）
    browser_launch_min_available_memory_mb: int = (
        1024  # 启动单个浏览器所需的「最小可用内存」(MB)，低于该值拒绝放行
    )
    browser_launch_reserved_memory_mb: int = (
        768  # 单个浏览器启动的内存预留额度(MB)：用于并发准入记账，避免瞬时放行打爆内存
    )
    browser_launch_admit_cooldown_ms: int = (
        1000  # 两次放行的最小间隔(ms)：给浏览器进程内存爬升留出时间
    )
    browser_launch_queue_tick_interval_ms: int = 1000  # 队列兜底轮询间隔(ms)
    browser_launch_queue_max_wait_time: int = (
        600  # 排队最大等待时间(秒)，超时抛错；0 表示不限
    )

    # ── 排队时长估算（ETA）──
    # 用「连续放行间隔」的实测均值 × 前方人数估算等待时长，供前端展示。
    # 仅采样「放行后队列仍有等待者」的间隔：此时放行节奏由内存释放 + 冷却共同决定，
    # 才是排队时长的有效信号；队列排空后的间隔只反映空闲时长，纳入会严重高估。
    browser_launch_eta_sample_window: int = 20  # 放行间隔采样窗口（最近 N 次连续放行）
    browser_launch_eta_min_samples: int = (
        3  # 生效所需的最少样本数；不足则退化为冷却下限估算（偏乐观，标记为不可信）
    )

    # ── 浏览器内存实测估算（实测校准准入额度，防止并发启动 OOM）──
    # 实测口径：按会话的 --user-data-dir 聚合 Chromium 进程树，累加 /proc 的 PSS
    # （共享内存按比例分摊，避免多进程重复计数）。本机实测参考：
    # 有头 Chromium 空页面 ≈ 435MB，重 DOM 页面 ≈ 465MB，每多开 1 个 page ≈ +15~20MB。
    browser_memory_estimate_enabled: bool = True  # 是否按实测占用自适应调整预留额度
    browser_memory_scan_interval: int = 15  # 全量扫描浏览器进程内存的间隔(秒)
    browser_memory_post_launch_delay: int = 10  # 启动完成后延迟多久采样(秒)，等内存爬升
    browser_memory_sample_window: int = 20  # 内存样本滑动窗口长度
    browser_memory_min_samples: int = 3  # 至少积累多少样本才用实测值覆盖基准值
    browser_memory_safety_factor: float = 1.1  # 实测值的安全系数（额外留出的余量）
    browser_memory_min_sample_mb: int = 64  # 低于该值的采样视为噪声（进程未真正起来），丢弃

    # ── 并发护栏（除内存外的硬性上限，双保险防 OOM）──
    browser_launch_max_memory_percent: float = (
        90.0  # 系统内存使用率红线(%)：超过则一律不放行，避免 OOM Killer 介入
    )
    browser_max_concurrent_instances: int = (
        0  # 同时存在的浏览器实例数上限（运行中+启动中）；0 表示仅按内存限制
    )

    # 浏览器页面数量限制配置
    browser_max_pages_per_context: int = 10  # 每个浏览器上下文的最大页面数

    # 工作流控制流嵌套深度限制
    workflow_max_nesting_depth: int = 10  # 最大嵌套深度（Loop/IfElse）

    # WebRTC 视频流配置（清晰度档位见计划书 §5.18）
    browser_webrtc_idle_timeout: int = 300  # WebRTC 流最大闲置时间（秒），默认5分钟
    browser_stream_medium_quality: int = 65  # 标清档 JPEG 质量（0-100）
    browser_stream_medium_max_fps: int = 15  # 标清档最大帧率
    browser_stream_medium_frame_max_width: int = 960  # 标清档浏览器侧分辨率上限
    browser_stream_medium_frame_max_height: int = 540
    browser_stream_degrade_quality: int = 50  # 流畅档（原「降级档」）JPEG 质量（0-100）
    browser_stream_degrade_max_fps: int = 5  # 流畅档最大帧率
    # 流畅档在浏览器侧降分辨率（screencast size），JPEG 编码/传输/解码同步降载
    browser_stream_degrade_frame_max_width: int = 640
    browser_stream_degrade_frame_max_height: int = 360

    # Alembic 数据库迁移配置
    alembic_auto_migrate: bool = True  # 是否在应用启动时自动执行数据库迁移
    alembic_upgrade_target: str = "heads"  # 迁移目标版本，默认为最新版本

    # 浏览器操作日志采集配置（用户未做任何设置时的服务端兜底值）
    action_log_default_enabled: bool = (
        False  # 默认是否采集操作日志（用户可按 action 覆盖）
    )
    action_log_max_payload_length: int = (
        4000  # params/result/variables 序列化后最大字符数
    )
    action_log_default_retention_days: int = 30  # 默认日志保留天数，0 表示永久保留


settings = Settings()
logger.info(f"Settings loaded\n{settings}")


class CONF:
    """
    配置类
    """

    class Path:
        """
        路径配置
        """

        logs = os.path.join(current_dir, "./logs")
        project_root = PROJECT_ROOT
        user_data_dir = os.path.join(PROJECT_ROOT, "user_data_dir")


__all__ = ["settings", "CONF", "PushChannelConfig"]
