/**
 * ecosystem.config.js — PM2 process manager config
 * Usage: pm2 start ecosystem.config.js
 */
module.exports = {
  apps: [
    {
      name        : 'notebooklm-proxy',
      script      : 'server.js',
      cwd         : __dirname,
      instances   : 1,          // must be 1 — single shared browser session
      autorestart : true,
      watch       : false,
      max_memory_restart: '800M',
      env: {
        NODE_ENV      : 'production',
        PORT          : '3001',
        // NOTEBOOK_URL and ALLOWED_ORIGIN are read from .env
      },
      log_date_format : 'YYYY-MM-DD HH:mm:ss',
      error_file  : './logs/err.log',
      out_file    : './logs/out.log',
      merge_logs  : true,
    },
  ],
};
