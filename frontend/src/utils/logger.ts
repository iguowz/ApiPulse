type LogLevel = 'debug' | 'info' | 'warn' | 'error'

const LOG_LEVELS: Record<LogLevel, number> = { debug: 0, info: 1, warn: 2, error: 3 }

// 生产环境仅输出 error，开发环境输出所有级别
const minLevel: LogLevel = import.meta.env.PROD ? 'error' : 'debug'

function log(level: LogLevel, ...args: unknown[]) {
  if (LOG_LEVELS[level] >= LOG_LEVELS[minLevel]) {
    const ts = new Date().toISOString()
    console[level](`[${level.toUpperCase()}] ${ts}`, ...args)
  }
}

const logger = {
  debug: (...args: unknown[]) => log('debug', ...args),
  info: (...args: unknown[]) => log('info', ...args),
  warn: (...args: unknown[]) => log('warn', ...args),
  error: (...args: unknown[]) => log('error', ...args),
}

export default logger
