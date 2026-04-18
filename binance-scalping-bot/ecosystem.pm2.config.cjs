const path = require('path');

const rootDir = __dirname;
const backendRuntimeDir = path.join(rootDir, 'backend', '.runtime', 'pm2');
const frontendRuntimeDir = path.join(rootDir, 'frontend', '.runtime', 'pm2');

module.exports = {
  apps: [
    {
      name: 'ml-candles-backend-9000',
      cwd: rootDir,
      script: './scripts/backend_pm2.sh',
      interpreter: '/usr/bin/env',
      interpreter_args: 'bash',
      env: {
        HOST: '0.0.0.0',
        PORT: '9000',
      },
      autorestart: true,
      restart_delay: 2000,
      max_restarts: 10,
      watch: false,
      merge_logs: true,
      out_file: path.join(backendRuntimeDir, 'ml-candles-backend-8005.out.log'),
      error_file: path.join(backendRuntimeDir, 'ml-candles-backend-8005.err.log'),
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    },
    {
      name: 'ml-candles-frontend-5199',
      cwd: rootDir,
      script: './scripts/frontend_pm2.sh',
      interpreter: '/usr/bin/env',
      interpreter_args: 'bash',
      env: {
        FRONTEND_HOST: '0.0.0.0',
        FRONTEND_PORT: '5199',
        PM2_NODE_BIN: '/home/thangnguyen/.nvm/versions/node/v20.19.0/bin/node',
      },
      autorestart: true,
      restart_delay: 2000,
      max_restarts: 10,
      watch: false,
      merge_logs: true,
      out_file: path.join(frontendRuntimeDir, 'ml-candles-frontend-5199.out.log'),
      error_file: path.join(frontendRuntimeDir, 'ml-candles-frontend-5199.err.log'),
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    },
  ],
};
