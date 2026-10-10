// Argus 运行目录。
// - ARGUS_ROOT 设置时，app/cache/logs 全部落在它下面（Linux 夜间 QA：$QA_HOME/argus-root，见 panoptes run-nightly.sh）。
// - 未设置时：macOS 沿用 Mini 的 ~/Library 布局；其他平台默认 ${XDG_STATE_HOME:-~/.local/state}/argus。
import { homedir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const home = homedir()
const root = process.env.ARGUS_ROOT
  || (process.platform === 'darwin' ? null : join(process.env.XDG_STATE_HOME || join(home, '.local/state'), 'argus'))

export const CODE_DIR = join(dirname(fileURLToPath(import.meta.url)), '..')

export const APP_DIR = root ? join(root, 'app') : join(home, 'Library/Application Support/PraiseZhu/pr-qa-agent')
export const STATE_DIR = join(APP_DIR, 'state')
export const DB_PATH = join(STATE_DIR, 'argus.sqlite')
export const LOCK_PATH = join(STATE_DIR, 'control.lock')
export const EVIDENCE_DIR = join(APP_DIR, 'evidence')
export const SECRETS_FILE = process.env.ARGUS_SECRETS_FILE ?? join(APP_DIR, 'secrets.env')

export const CACHE_DIR = root ? join(root, 'cache') : join(home, 'Library/Caches/PraiseZhu/pr-qa-agent')
export const RUNS_DIR = join(CACHE_DIR, 'runs')
export const MIRRORS_DIR = join(CACHE_DIR, 'mirrors')
export const NPM_CACHE_DIR = join(CACHE_DIR, 'npm')
export const UV_CACHE_DIR = join(CACHE_DIR, 'uv')
export const UV_PYTHON_DIR = join(CACHE_DIR, 'uv-python')

export const LOGS_DIR = root ? join(root, 'logs') : join(home, 'Library/Logs/PraiseZhu/pr-qa-agent')
export const CONTROL_LOG = join(LOGS_DIR, 'control.log')

export const REPORTS_DIR = process.env.ARGUS_REPORTS_DIR ?? (root ? join(root, 'reports') : join(APP_DIR, 'reports'))

export const ALL_DIRS = [STATE_DIR, EVIDENCE_DIR, RUNS_DIR, MIRRORS_DIR, NPM_CACHE_DIR, UV_CACHE_DIR, UV_PYTHON_DIR, LOGS_DIR]
