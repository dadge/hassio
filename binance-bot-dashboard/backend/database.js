const Database = require('better-sqlite3');
const path = require('path');
const fs = require('fs');

// Database path - /data for Hassio, local for development
const DATA_DIR = process.env.DATA_DIR || path.join(__dirname, 'data');
const DB_PATH = path.join(DATA_DIR, 'botdata.sqlite');

// Ensure data directory exists
if (!fs.existsSync(DATA_DIR)) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
}

console.log(`📁 Database path: ${DB_PATH}`);

const db = new Database(DB_PATH);

// Enable WAL mode for better performance
db.pragma('journal_mode = WAL');

/**
 * Initialize database tables
 */
function initDatabase() {
  // History table - stores saved bot data entries
  db.exec(`
    CREATE TABLE IF NOT EXISTS history (
      id TEXT PRIMARY KEY,
      savedAt TEXT NOT NULL,
      botDataText TEXT,
      parsedBotsCount INTEGER DEFAULT 0,
      totalGridProfitEUR REAL DEFAULT 0,
      totalGridProfitUSDC REAL DEFAULT 0,
      closedBotsConfig TEXT
    )
  `);

  // Config table - stores current configuration
  db.exec(`
    CREATE TABLE IF NOT EXISTS config (
      key TEXT PRIMARY KEY,
      value TEXT
    )
  `);

  console.log('✅ Database initialized');
}

// Initialize on load
initDatabase();

/**
 * Generate unique ID
 */
function generateId() {
  return Date.now().toString(36) + Math.random().toString(36).substring(2);
}

// ============ HISTORY OPERATIONS ============

/**
 * Get all history entries
 */
function getAllHistory() {
  const stmt = db.prepare('SELECT * FROM history ORDER BY savedAt DESC');
  const rows = stmt.all();

  return rows.map((row) => ({
    id: row.id,
    savedAt: row.savedAt,
    botDataText: row.botDataText,
    parsedBotsCount: row.parsedBotsCount,
    totalGridProfitEUR: row.totalGridProfitEUR,
    totalGridProfitUSDC: row.totalGridProfitUSDC,
    closedBotsConfig: row.closedBotsConfig ? JSON.parse(row.closedBotsConfig) : null,
  }));
}

/**
 * Get single history entry by ID
 */
function getHistoryById(id) {
  const stmt = db.prepare('SELECT * FROM history WHERE id = ?');
  const row = stmt.get(id);

  if (!row) return null;

  return {
    id: row.id,
    savedAt: row.savedAt,
    botDataText: row.botDataText,
    parsedBotsCount: row.parsedBotsCount,
    totalGridProfitEUR: row.totalGridProfitEUR,
    totalGridProfitUSDC: row.totalGridProfitUSDC,
    closedBotsConfig: row.closedBotsConfig ? JSON.parse(row.closedBotsConfig) : null,
  };
}

/**
 * Add new history entry
 */
function addHistoryEntry(entry) {
  const id = entry.id || generateId();
  const savedAt = entry.savedAt || new Date().toISOString();

  const stmt = db.prepare(`
    INSERT INTO history (id, savedAt, botDataText, parsedBotsCount, totalGridProfitEUR, totalGridProfitUSDC, closedBotsConfig)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `);

  stmt.run(
    id,
    savedAt,
    entry.botDataText || '',
    entry.parsedBotsCount || 0,
    entry.totalGridProfitEUR || 0,
    entry.totalGridProfitUSDC || 0,
    entry.closedBotsConfig ? JSON.stringify(entry.closedBotsConfig) : null,
  );

  return { id, savedAt, ...entry };
}

/**
 * Delete history entry by ID
 */
function deleteHistoryEntry(id) {
  const stmt = db.prepare('DELETE FROM history WHERE id = ?');
  const result = stmt.run(id);
  return result.changes > 0;
}

/**
 * Clear all history
 */
function clearHistory() {
  const stmt = db.prepare('DELETE FROM history');
  const result = stmt.run();
  return result.changes;
}

// ============ CONFIG OPERATIONS ============

/**
 * Get config value by key
 */
function getConfig(key) {
  const stmt = db.prepare('SELECT value FROM config WHERE key = ?');
  const row = stmt.get(key);

  if (!row) return null;

  try {
    return JSON.parse(row.value);
  } catch {
    return row.value;
  }
}

/**
 * Set config value
 */
function setConfig(key, value) {
  const stmt = db.prepare(`
    INSERT OR REPLACE INTO config (key, value)
    VALUES (?, ?)
  `);

  const valueStr = typeof value === 'string' ? value : JSON.stringify(value);
  stmt.run(key, valueStr);
  return true;
}

/**
 * Get all config as object
 */
function getAllConfig() {
  const stmt = db.prepare('SELECT key, value FROM config');
  const rows = stmt.all();

  const config = {};
  for (const row of rows) {
    try {
      config[row.key] = JSON.parse(row.value);
    } catch {
      config[row.key] = row.value;
    }
  }
  return config;
}

/**
 * Delete config key
 */
function deleteConfig(key) {
  const stmt = db.prepare('DELETE FROM config WHERE key = ?');
  const result = stmt.run(key);
  return result.changes > 0;
}

module.exports = {
  db,
  getAllHistory,
  getHistoryById,
  addHistoryEntry,
  deleteHistoryEntry,
  clearHistory,
  getConfig,
  setConfig,
  getAllConfig,
  deleteConfig,
};
