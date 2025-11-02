-- schema.sql
CREATE DATABASE IF NOT EXISTS asinscanner CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
USE asinscanner;

-- ASINs to monitor
CREATE TABLE IF NOT EXISTS asins (
  id INT AUTO_INCREMENT PRIMARY KEY,
  asin VARCHAR(32) NOT NULL UNIQUE,
  note VARCHAR(255) DEFAULT NULL,
  active TINYINT(1) DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_checked TIMESTAMP NULL DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Regex patterns
CREATE TABLE IF NOT EXISTS patterns (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  pattern TEXT NOT NULL,
  flags INT DEFAULT 0, -- python re flags stored as int bitmask (e.g. re.IGNORECASE -> 2)
  description TEXT DEFAULT NULL,
  active TINYINT(1) DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Results of scans
CREATE TABLE IF NOT EXISTS results (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  asin_id INT NOT NULL,
  pattern_id INT NOT NULL,
  matched_text TEXT NOT NULL,
  matched_group TEXT DEFAULT NULL,
  source_url VARCHAR(1024) DEFAULT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (asin_id) REFERENCES asins(id) ON DELETE CASCADE,
  FOREIGN KEY (pattern_id) REFERENCES patterns(id) ON DELETE CASCADE,
  INDEX idx_asin_created (asin_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
