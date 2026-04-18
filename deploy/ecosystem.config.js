module.exports = {
  apps: [{
    name: 'scamhound',
    cwd: '/opt/scamhound/scamhound',
    script: 'main.py',
    interpreter: '/opt/scamhound/venv/bin/python',
    env: {
      // Keys loaded from .env file
    },
    // Watch for changes (optional)
    watch: false,
    // Auto-restart on failure
    autorestart: true,
    max_restarts: 10,
    restart_delay: 5000,
    // Logging
    log_file: '/opt/scamhound/logs/scamhound.log',
    error_file: '/opt/scamhound/logs/scamhound-error.log',
    out_file: '/opt/scamhound/logs/scamhound-out.log',
    merge_logs: true,
    // Timestamp logs
    time: true,
  }]
}
