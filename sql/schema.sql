-- MySQL Schema for Audi A4 B5 1.9 TDI (AFN/EDC15) Telemetry Logging
-- Database: audi_diag
-- Engine: InnoDB for transactional integrity and row-level locking

CREATE DATABASE IF NOT EXISTS `audi_diag` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `audi_diag`;

-- Sessions table: Tracks each diagnostic/connection session
CREATE TABLE IF NOT EXISTS `sessions` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `vin` VARCHAR(17) DEFAULT NULL,
    `ecu_part_number` VARCHAR(20) DEFAULT NULL,
    `ecu_software_version` VARCHAR(20) DEFAULT NULL,
    `engine_code` VARCHAR(10) DEFAULT 'AFN',
    `ecu_type` VARCHAR(20) DEFAULT 'EDC15',
    `adapter_type` VARCHAR(30) DEFAULT 'FTDI_KKL',
    `port` VARCHAR(50) DEFAULT '/dev/ttyUSB0',
    `started_at` TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `ended_at` TIMESTAMP(3) DEFAULT NULL,
    `total_frames` BIGINT UNSIGNED NOT NULL DEFAULT 0,
    `dropped_frames` BIGINT UNSIGNED NOT NULL DEFAULT 0,
    `checksum_errors` BIGINT UNSIGNED NOT NULL DEFAULT 0,
    `notes` TEXT DEFAULT NULL,
    PRIMARY KEY (`id`),
    INDEX `idx_started_at` (`started_at`),
    INDEX `idx_vin` (`vin`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Measuring Block 003: MAF (Mass Air Flow) & RPM
-- Block 003 typically contains: RPM, MAF Actual, MAF Specified, Engine Load, Throttle Position
CREATE TABLE IF NOT EXISTS `measuring_block_003` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `session_id` BIGINT UNSIGNED NOT NULL,
    `timestamp` TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `relative_ms` INT UNSIGNED NOT NULL COMMENT 'Milliseconds since session start',
    `rpm` SMALLINT UNSIGNED NOT NULL COMMENT 'Engine RPM (0-8000)',
    `maf_actual_mg_stroke` DECIMAL(8,2) NOT NULL COMMENT 'Actual MAF in mg/stroke',
    `maf_specified_mg_stroke` DECIMAL(8,2) NOT NULL COMMENT 'Specified MAF in mg/stroke',
    `engine_load_pct` TINYINT UNSIGNED NOT NULL COMMENT 'Engine load percentage (0-100%)',
    `throttle_position_pct` TINYINT UNSIGNED NOT NULL COMMENT 'Throttle position percentage (0-100%)',
    `iq_actual_mg_stroke` DECIMAL(6,2) DEFAULT NULL COMMENT 'Injection quantity actual',
    `iq_specified_mg_stroke` DECIMAL(6,2) DEFAULT NULL COMMENT 'Injection quantity specified',
    `raw_block_data` VARBINARY(64) NOT NULL COMMENT 'Raw 12-byte block payload for forensic analysis',
    `checksum_valid` BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (`id`),
    CONSTRAINT `fk_mb003_session` FOREIGN KEY (`session_id`) REFERENCES `sessions` (`id`) ON DELETE CASCADE,
    INDEX `idx_session_timestamp` (`session_id`, `timestamp`),
    INDEX `idx_relative_ms` (`session_id`, `relative_ms`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Measuring Block 007: Temperatures
-- Block 007 typically contains: Coolant Temp, Intake Air Temp, Fuel Temp, Oil Temp, Ambient Temp
CREATE TABLE IF NOT EXISTS `measuring_block_007` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `session_id` BIGINT UNSIGNED NOT NULL,
    `timestamp` TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `relative_ms` INT UNSIGNED NOT NULL,
    `coolant_temp_c` SMALLINT NOT NULL COMMENT 'Coolant temperature in °C (signed, -40 to 200)',
    `intake_air_temp_c` SMALLINT NOT NULL COMMENT 'Intake air temperature in °C',
    `fuel_temp_c` SMALLINT DEFAULT NULL COMMENT 'Fuel temperature in °C',
    `oil_temp_c` SMALLINT DEFAULT NULL COMMENT 'Oil temperature in °C',
    `ambient_temp_c` SMALLINT DEFAULT NULL COMMENT 'Ambient temperature in °C',
    `egr_temp_c` SMALLINT DEFAULT NULL COMMENT 'EGR temperature in °C (if equipped)',
    `raw_block_data` VARBINARY(64) NOT NULL,
    `checksum_valid` BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (`id`),
    CONSTRAINT `fk_mb007_session` FOREIGN KEY (`session_id`) REFERENCES `sessions` (`id`) ON DELETE CASCADE,
    INDEX `idx_session_timestamp` (`session_id`, `timestamp`),
    INDEX `idx_relative_ms` (`session_id`, `relative_ms`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Measuring Block 011: MAP/Boost Pressure
-- Block 011 typically contains: MAP Actual, MAP Specified, Boost Pressure, Wastegate Duty Cycle, N75 Valve Duty
CREATE TABLE IF NOT EXISTS `measuring_block_011` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `session_id` BIGINT UNSIGNED NOT NULL,
    `timestamp` TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `relative_ms` INT UNSIGNED NOT NULL,
    `map_actual_mbar` SMALLINT UNSIGNED NOT NULL COMMENT 'Actual MAP in mbar (0-3000)',
    `map_specified_mbar` SMALLINT UNSIGNED NOT NULL COMMENT 'Specified MAP in mbar',
    `boost_pressure_mbar` SMALLINT NOT NULL COMMENT 'Boost pressure (MAP - 1000) in mbar',
    `wastegate_duty_pct` TINYINT UNSIGNED NOT NULL COMMENT 'Wastegate/N75 duty cycle (0-100%)',
    `n75_valve_duty_pct` TINYINT UNSIGNED DEFAULT NULL COMMENT 'N75 valve duty cycle (0-100%)',
    `egr_duty_pct` TINYINT UNSIGNED DEFAULT NULL COMMENT 'EGR valve duty cycle (0-100%)',
    `raw_block_data` VARBINARY(64) NOT NULL,
    `checksum_valid` BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (`id`),
    CONSTRAINT `fk_mb011_session` FOREIGN KEY (`session_id`) REFERENCES `sessions` (`id`) ON DELETE CASCADE,
    INDEX `idx_session_timestamp` (`session_id`, `timestamp`),
    INDEX `idx_relative_ms` (`session_id`, `relative_ms`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Error/Event Log: Protocol errors, connection drops, checksum failures
CREATE TABLE IF NOT EXISTS `diagnostic_events` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `session_id` BIGINT UNSIGNED NOT NULL,
    `timestamp` TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `event_type` ENUM('CONNECT', 'DISCONNECT', 'TIMEOUT', 'CHECKSUM_ERROR', 'BLOCK_ERROR', 'BAUD_SWITCH', 'INIT_FAILED', 'RECONNECT', 'BUFFER_OVERFLOW') NOT NULL,
    `severity` ENUM('INFO', 'WARNING', 'ERROR', 'CRITICAL') NOT NULL DEFAULT 'INFO',
    `block_number` SMALLINT UNSIGNED DEFAULT NULL,
    `expected_bytes` SMALLINT UNSIGNED DEFAULT NULL,
    `received_bytes` SMALLINT UNSIGNED DEFAULT NULL,
    `error_message` TEXT DEFAULT NULL,
    `raw_data` VARBINARY(256) DEFAULT NULL,
    PRIMARY KEY (`id`),
    CONSTRAINT `fk_events_session` FOREIGN KEY (`session_id`) REFERENCES `sessions` (`id`) ON DELETE CASCADE,
    INDEX `idx_session_timestamp` (`session_id`, `timestamp`),
    INDEX `idx_event_type` (`session_id`, `event_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ECU Identification Cache: Store parsed ECU identification data
CREATE TABLE IF NOT EXISTS `ecu_identification` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `session_id` BIGINT UNSIGNED NOT NULL,
    `part_number` VARCHAR(20) DEFAULT NULL,
    `software_version` VARCHAR(20) DEFAULT NULL,
    `engine_code` VARCHAR(10) DEFAULT NULL,
    `vehicle_identification` VARCHAR(40) DEFAULT NULL,
    `date_of_manufacture` DATE DEFAULT NULL,
    `coding` VARCHAR(20) DEFAULT NULL,
    `raw_identification_block` VARBINARY(512) NOT NULL,
    `parsed_at` TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (`id`),
    CONSTRAINT `fk_ecu_id_session` FOREIGN KEY (`session_id`) REFERENCES `sessions` (`id`) ON DELETE CASCADE,
    UNIQUE KEY `uk_session` (`session_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Views for common queries

-- View: Latest telemetry per session (for real-time dashboard)
CREATE OR REPLACE VIEW `v_latest_telemetry` AS
SELECT 
    s.id AS session_id,
    s.started_at,
    mb3.rpm,
    mb3.maf_actual_mg_stroke,
    mb3.maf_specified_mg_stroke,
    mb3.engine_load_pct,
    mb7.coolant_temp_c,
    mb7.intake_air_temp_c,
    mb11.map_actual_mbar,
    mb11.map_specified_mbar,
    mb11.boost_pressure_mbar,
    mb11.wastegate_duty_pct,
    GREATEST(mb3.timestamp, mb7.timestamp, mb11.timestamp) AS last_update
FROM sessions s
LEFT JOIN measuring_block_003 mb3 ON mb3.session_id = s.id
LEFT JOIN measuring_block_007 mb7 ON mb7.session_id = s.id
LEFT JOIN measuring_block_011 mb11 ON mb11.session_id = s.id
WHERE s.ended_at IS NULL
  AND mb3.id = (SELECT MAX(id) FROM measuring_block_003 WHERE session_id = s.id)
  AND mb7.id = (SELECT MAX(id) FROM measuring_block_007 WHERE session_id = s.id)
  AND mb11.id = (SELECT MAX(id) FROM measuring_block_011 WHERE session_id = s.id);

-- View: Session summary statistics
CREATE OR REPLACE VIEW `v_session_summary` AS
SELECT 
    s.id,
    s.vin,
    s.ecu_part_number,
    s.engine_code,
    s.started_at,
    s.ended_at,
    TIMESTAMPDIFF(SECOND, s.started_at, COALESCE(s.ended_at, NOW())) AS duration_seconds,
    s.total_frames,
    s.dropped_frames,
    s.checksum_errors,
    CASE WHEN s.total_frames > 0 
         THEN ROUND(100.0 * s.dropped_frames / s.total_frames, 2) 
         ELSE 0 END AS drop_rate_pct,
    (SELECT COUNT(*) FROM measuring_block_003 WHERE session_id = s.id) AS mb003_count,
    (SELECT COUNT(*) FROM measuring_block_007 WHERE session_id = s.id) AS mb007_count,
    (SELECT COUNT(*) FROM measuring_block_011 WHERE session_id = s.id) AS mb011_count,
    (SELECT COUNT(*) FROM diagnostic_events WHERE session_id = s.id AND severity IN ('ERROR','CRITICAL')) AS error_count
FROM sessions s;

-- Stored Procedure: Create new session with atomic session start
DELIMITER $$
CREATE PROCEDURE `sp_start_session`(
    IN p_vin VARCHAR(17),
    IN p_ecu_part VARCHAR(20),
    IN p_ecu_sw VARCHAR(20),
    IN p_port VARCHAR(50),
    OUT p_session_id BIGINT UNSIGNED
)
BEGIN
    INSERT INTO sessions (vin, ecu_part_number, ecu_software_version, port, started_at)
    VALUES (p_vin, p_ecu_part, p_ecu_sw, p_port, CURRENT_TIMESTAMP(3));
    SET p_session_id = LAST_INSERT_ID();
END$$
DELIMITER ;

-- Stored Procedure: End session and update stats
DELIMITER $$
CREATE PROCEDURE `sp_end_session`(IN p_session_id BIGINT UNSIGNED)
BEGIN
    UPDATE sessions 
    SET ended_at = CURRENT_TIMESTAMP(3),
        total_frames = (
            SELECT COUNT(*) FROM measuring_block_003 WHERE session_id = p_session_id
        ) + (
            SELECT COUNT(*) FROM measuring_block_007 WHERE session_id = p_session_id
        ) + (
            SELECT COUNT(*) FROM measuring_block_011 WHERE session_id = p_session_id
        ),
        dropped_frames = (
            SELECT COUNT(*) FROM diagnostic_events 
            WHERE session_id = p_session_id AND event_type IN ('TIMEOUT', 'BLOCK_ERROR')
        ),
        checksum_errors = (
            SELECT COUNT(*) FROM diagnostic_events 
            WHERE session_id = p_session_id AND event_type = 'CHECKSUM_ERROR'
        )
    WHERE id = p_session_id;
END$$
DELIMITER ;

-- Index optimization for high-frequency inserts
-- These are already defined above but listed here for clarity:
-- All measuring block tables have composite index (session_id, timestamp)
-- All measuring block tables have index (session_id, relative_ms)
-- Events table has index on (session_id, event_type)

-- Grant permissions for application user (adjust as needed)
-- CREATE USER 'audi_diag'@'localhost' IDENTIFIED BY 'secure_password';
-- GRANT INSERT, SELECT, UPDATE ON audi_diag.* TO 'audi_diag'@'localhost';
-- GRANT EXECUTE ON PROCEDURE audi_diag.sp_start_session TO 'audi_diag'@'localhost';
-- GRANT EXECUTE ON PROCEDURE audi_diag.sp_end_session TO 'audi_diag'@'localhost';