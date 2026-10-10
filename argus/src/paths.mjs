// Argus 运行目录。全部落在 Mini 本地非 iCloud 路径；ARGUS_ROOT 仅供测试重定向。
import { homedir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = process.env.ARGUS_ROOT
const home = homedir()

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

export const ALL_DIRS = [STATE_DIR, EVIDENCE_DIR, RUNS_DIR, MIRRORS_DIR, NPM_CACHE_DIR, UV_CACHE_DIR, UV_PYTHON_DIR, LOGS_DIR]
