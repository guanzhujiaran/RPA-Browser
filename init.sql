CREATE DATABASE IF NOT EXISTS browser_info CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE browser_info;

-- 创建用户浏览器信息表
CREATE TABLE IF NOT EXISTS user_browser_info (
    id INT AUTO_INCREMENT PRIMARY KEY,
    browser_token VARCHAR(36) UNIQUE NOT NULL,
    fingerprint INT NOT NULL,
    fingerprint_platform ENUM('windows', 'linux', 'macos') NOT NULL DEFAULT 'windows',
    fingerprint_platform_version VARCHAR(50),
    fingerprint_browser ENUM('chrome', 'Edge', 'Opera', 'Vivaldi'),
    fingerprint_brand_version VARCHAR(50),
    fingerprint_hardware_concurrency INT,
    fingerprint_gpu_vendor VARCHAR(100),
    fingerprint_gpu_renderer VARCHAR(100),
    lang VARCHAR(20),
    accept_lang VARCHAR(20),
    timezone VARCHAR(50) DEFAULT 'Asia/Shanghai',
    proxy_server VARCHAR(200),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_browser_token (browser_token),
    UNIQUE KEY unique_fingerprint (fingerprint)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;