const express = require('express');
const cors = require('cors');
const https = require('https');
const database = require('./database');

const app = express();
const PORT = process.env.PORT || 3000;

// Mode: 'mock' or 'live' - defaults to 'mock' in development
const MODE = process.env.MODE || 'mock';

console.log(`📊 Backend mode: ${MODE}`);

// Enable CORS for frontend
app.use(cors());
app.use(express.json());

// Symbols to fetch from Binance
const SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'EURUSDT', 'BTCEUR', 'ETHEUR'];

/**
 * Mock exchange rates (used in development and mock mode)
 * Values aligned with frontend MockExchangeRateService
 */
const MOCK_RATES = [
  { symbol: 'BTCUSDT', price: 63173.5, baseAsset: 'BTC', quoteAsset: 'USDT' },
  { symbol: 'ETHUSDT', price: 1855.45, baseAsset: 'ETH', quoteAsset: 'USDT' },
  { symbol: 'BNBUSDT', price: 586.95, baseAsset: 'BNB', quoteAsset: 'USDT' },
  { symbol: 'EURUSDT', price: 1.1529, baseAsset: 'EUR', quoteAsset: 'USDT' },
  { symbol: 'BTCEUR', price: 58138.33, baseAsset: 'BTC', quoteAsset: 'EUR' },
  { symbol: 'ETHEUR', price: 1707.9, baseAsset: 'ETH', quoteAsset: 'EUR' },
];

/**
 * Get mock exchange rates
 */
function getMockRates() {
  console.log(`[${new Date().toISOString()}] Returning mock rates`);
  return MOCK_RATES;
}

/**
 * Fetch exchange rates from Binance API
 */
function fetchBinanceRates() {
  return new Promise((resolve, reject) => {
    const symbolsParam = encodeURIComponent(JSON.stringify(SYMBOLS));

    const options = {
      hostname: 'api.binance.com',
      port: 443,
      path: `/api/v3/ticker/price?symbols=${symbolsParam}`,
      method: 'GET',
      // Ignorer les erreurs de certificat (utile si le proxy d'entreprise intercepte le SSL)
      rejectUnauthorized: false,
    };

    const req = https.request(options, (res) => {
      let data = '';

      res.on('data', (chunk) => {
        data += chunk;
      });

      res.on('end', () => {
        try {
          const rates = JSON.parse(data);
          if (Array.isArray(rates)) {
            resolve(rates);
          } else if (rates.code && rates.msg) {
            // Binance error response
            reject(new Error(`Binance API error: ${rates.msg}`));
          } else {
            resolve(rates);
          }
        } catch (error) {
          reject(new Error('Failed to parse Binance response: ' + data.substring(0, 100)));
        }
      });
    });

    req.on('error', (error) => {
      reject(error);
    });

    req.end();
  });
}

/**
 * Extract base asset from symbol
 */
function extractBaseAsset(symbol) {
  const quoteAssets = ['EUR', 'USDC', 'USDT', 'BTC', 'ETH', 'BNB'];
  for (const quote of quoteAssets) {
    if (symbol.endsWith(quote)) {
      return symbol.slice(0, -quote.length);
    }
  }
  return symbol;
}

/**
 * Extract quote asset from symbol
 */
function extractQuoteAsset(symbol) {
  const quoteAssets = ['EUR', 'USDC', 'USDT', 'BTC', 'ETH', 'BNB'];
  for (const quote of quoteAssets) {
    if (symbol.endsWith(quote)) {
      return quote;
    }
  }
  return '';
}

/**
 * GET /api/rates - Fetch exchange rates from Binance or return mock data
 */
app.get('/api/rates', async (req, res) => {
  console.log(`[${new Date().toISOString()}] GET /api/rates (mode: ${MODE})`);

  try {
    let rates;

    if (MODE === 'mock') {
      // Return mock data
      rates = getMockRates();
    } else {
      // Fetch from Binance API
      const binanceRates = await fetchBinanceRates();

      // Transform to our format
      rates = binanceRates.map((rate) => ({
        symbol: rate.symbol,
        price: parseFloat(rate.price),
        baseAsset: extractBaseAsset(rate.symbol),
        quoteAsset: extractQuoteAsset(rate.symbol),
      }));

      console.log(`[${new Date().toISOString()}] Fetched ${rates.length} rates from Binance`);
    }

    res.json(rates);
  } catch (error) {
    console.error(`[${new Date().toISOString()}] Error fetching rates:`, error.message);

    // In case of error, fallback to mock data
    if (MODE === 'live') {
      console.log(`[${new Date().toISOString()}] Falling back to mock data due to error`);
      res.json(getMockRates());
    } else {
      res.status(500).json({ error: 'Failed to fetch exchange rates', message: error.message });
    }
  }
});

/**
 * Health check endpoint
 */
app.get('/api/health', (req, res) => {
  res.json({
    status: 'ok',
    mode: MODE,
    timestamp: new Date().toISOString(),
  });
});

/**
 * Get current mode
 */
app.get('/api/mode', (req, res) => {
  res.json({ mode: MODE });
});

// ============ HISTORY ENDPOINTS ============

/**
 * GET /api/history - Get all history entries
 */
app.get('/api/history', (req, res) => {
  console.log(`[${new Date().toISOString()}] GET /api/history`);
  try {
    const history = database.getAllHistory();
    res.json(history);
  } catch (error) {
    console.error('Error getting history:', error);
    res.status(500).json({ error: 'Failed to get history', message: error.message });
  }
});

/**
 * GET /api/history/:id - Get single history entry
 */
app.get('/api/history/:id', (req, res) => {
  console.log(`[${new Date().toISOString()}] GET /api/history/${req.params.id}`);
  try {
    const entry = database.getHistoryById(req.params.id);
    if (!entry) {
      return res.status(404).json({ error: 'Entry not found' });
    }
    res.json(entry);
  } catch (error) {
    console.error('Error getting history entry:', error);
    res.status(500).json({ error: 'Failed to get history entry', message: error.message });
  }
});

/**
 * POST /api/history - Add new history entry
 */
app.post('/api/history', (req, res) => {
  console.log(`[${new Date().toISOString()}] POST /api/history`);
  try {
    const entry = database.addHistoryEntry(req.body);
    res.status(201).json(entry);
  } catch (error) {
    console.error('Error adding history entry:', error);
    res.status(500).json({ error: 'Failed to add history entry', message: error.message });
  }
});

/**
 * DELETE /api/history/:id - Delete history entry
 */
app.delete('/api/history/:id', (req, res) => {
  console.log(`[${new Date().toISOString()}] DELETE /api/history/${req.params.id}`);
  try {
    const deleted = database.deleteHistoryEntry(req.params.id);
    if (!deleted) {
      return res.status(404).json({ error: 'Entry not found' });
    }
    res.json({ success: true });
  } catch (error) {
    console.error('Error deleting history entry:', error);
    res.status(500).json({ error: 'Failed to delete history entry', message: error.message });
  }
});

/**
 * DELETE /api/history - Clear all history
 */
app.delete('/api/history', (req, res) => {
  console.log(`[${new Date().toISOString()}] DELETE /api/history (clear all)`);
  try {
    const count = database.clearHistory();
    res.json({ success: true, deletedCount: count });
  } catch (error) {
    console.error('Error clearing history:', error);
    res.status(500).json({ error: 'Failed to clear history', message: error.message });
  }
});

// ============ CONFIG ENDPOINTS ============

/**
 * GET /api/config - Get all config
 */
app.get('/api/config', (req, res) => {
  console.log(`[${new Date().toISOString()}] GET /api/config`);
  try {
    const config = database.getAllConfig();
    res.json(config);
  } catch (error) {
    console.error('Error getting config:', error);
    res.status(500).json({ error: 'Failed to get config', message: error.message });
  }
});

/**
 * GET /api/config/:key - Get specific config value
 * Returns { key, value: null } if key doesn't exist (not 404)
 */
app.get('/api/config/:key', (req, res) => {
  console.log(`[${new Date().toISOString()}] GET /api/config/${req.params.key}`);
  try {
    const value = database.getConfig(req.params.key);
    // Return null value instead of 404 - allows frontend to handle missing keys gracefully
    res.json({ key: req.params.key, value: value });
  } catch (error) {
    console.error('Error getting config:', error);
    res.status(500).json({ error: 'Failed to get config', message: error.message });
  }
});

/**
 * PUT /api/config/:key - Set config value
 */
app.put('/api/config/:key', (req, res) => {
  console.log(`[${new Date().toISOString()}] PUT /api/config/${req.params.key}`);
  try {
    const { value } = req.body;
    database.setConfig(req.params.key, value);
    res.json({ key: req.params.key, value });
  } catch (error) {
    console.error('Error setting config:', error);
    res.status(500).json({ error: 'Failed to set config', message: error.message });
  }
});

/**
 * POST /api/config - Set multiple config values
 */
app.post('/api/config', (req, res) => {
  console.log(`[${new Date().toISOString()}] POST /api/config`);
  try {
    const config = req.body;
    for (const [key, value] of Object.entries(config)) {
      database.setConfig(key, value);
    }
    res.json({ success: true, config });
  } catch (error) {
    console.error('Error setting config:', error);
    res.status(500).json({ error: 'Failed to set config', message: error.message });
  }
});

/**
 * DELETE /api/config/:key - Delete config key
 */
app.delete('/api/config/:key', (req, res) => {
  console.log(`[${new Date().toISOString()}] DELETE /api/config/${req.params.key}`);
  try {
    const deleted = database.deleteConfig(req.params.key);
    if (!deleted) {
      return res.status(404).json({ error: 'Config key not found' });
    }
    res.json({ success: true });
  } catch (error) {
    console.error('Error deleting config:', error);
    res.status(500).json({ error: 'Failed to delete config', message: error.message });
  }
});

// Start server
app.listen(PORT, () => {
  console.log(`🚀 Binance Bot Backend running on port ${PORT}`);
  console.log(`📊 Mode: ${MODE}`);
  console.log(`📊 Exchange rates endpoint: http://localhost:${PORT}/api/rates`);
  console.log(`📊 History endpoint: http://localhost:${PORT}/api/history`);
  console.log(`📊 Config endpoint: http://localhost:${PORT}/api/config`);
});
