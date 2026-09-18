# -*- coding: utf-8 -*-
"""
小爱课程表 · HNUST 教务助手 — Flask Web 应用

路由说明:
  /                   → 前端页面 (SPA 单页)
  /api/config         → 获取配置状态
  /api/login          → 小米账号密码登录
  /api/login/qr-*     → 扫码登录流程
  /api/login/userinfo → userinfo.txt 直接导入（新模式）
  /api/tables         → 课表 CRUD
  /api/sync           → 课表同步
  /api/capture/*      → 教务捕获
  /api/export         → 数据导出
  /api/diagnose       -> 连通性诊断
"""

import os
import sys
import json
import time
import uuid
import threading
import logging
import requests
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, request, jsonify, session, send_from_directory

# ─── Setup ───────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from core import (
    XiaomiAuthenticator, XiaoAiCourse, QiangZhiParser,
    HNUSTCapturer, SCHEDULE_PRESETS, generate_time_table,
    semester_name, suggest_semester_terms, current_semester_year,
    infer_sections_from_courses, apply_rule_overrides,
    SCHEDULE_RULES, SLOT_DURATION, INNER_BREAK, LONG_BREAK, GROUP_SIZE,
    DEFAULT_TOTAL_SECTIONS,
    xiaoai_qr_login,
    load_user_info,       # 新增: userinfo.txt 直读
    USERINFO_FILE,        # 新增: 用户信息文件路径
    load_dotenv,          # 新增: .env 加载（开源脱敏）
    get_xiaomi_credentials,  # 新增: 应用凭证读取
)

app = Flask(__name__)

# Persist secret key across restarts (stored in data dir)
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

SECRET_KEY_FILE = os.path.join(DATA_DIR, '.secret_key')
if os.path.exists(SECRET_KEY_FILE):
    with open(SECRET_KEY_FILE, 'r') as f:
        app.secret_key = f.read().strip()
else:
    key = os.urandom(32).hex()
    with open(SECRET_KEY_FILE, 'w') as f:
        f.write(key)
    app.secret_key = key

CONFIG_FILE = os.path.join(DATA_DIR, 'aischedule_config.json')
LOCAL_KB_FILE = os.path.join(DATA_DIR, 'xskb_list.do.txt')

# 全局进度状态字典（用于异步任务的前端轮询）
_progress_lock = threading.Lock()
capture_progress = {}
login_verify_progress = {}
qr_login_progress = {}
sync_progress = {}


def _update_progress(target, **kwargs):
    """线程安全地更新进度字典"""
    with _progress_lock:
        target.update(kwargs)


def _clear_progress(target):
    """线程安全地清空进度字典"""
    with _progress_lock:
        target.clear()

# ─── Logging ─────────────────────────────────────────────────────────
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'flask_debug.log')
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8'),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger('aischedule')


# ════════════════════════════════════════════════════════════════════════
#  Helper Functions
# ════════════════════════════════════════════════════════════════════════

def load_config():
    """加载本地配置文件"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log.error(f'Config load failed: {e}')
    return {}


def save_config(cfg):
    """保存配置到本地文件 (原子写入)"""
    try:
        tmp = CONFIG_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CONFIG_FILE)
    except IOError as e:
        log.error(f'Config save failed: {e}')


def get_auth_token():
    """获取认证 token：优先 session，其次 X-Auth-Token 请求头。

    token 双写机制：登录成功后前端同时持有 token（localStorage），
    服务重启/cookie 失效时仍可通过请求头完成认证。
    """
    return session.get('api_token') or request.headers.get('X-Auth-Token') or None


def require_login(f):
    """装饰器：确保用户已登录才可访问 API"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not get_auth_token():
            # 诊断日志：401 时记录客户端到底带了什么凭证
            cookie_header = request.headers.get('Cookie', '')
            log.warning(
                f'401 diagnose: path={request.path} '
                f'x_auth_token={"YES" if request.headers.get("X-Auth-Token") else "NO"} '
                f'cookie={"YES len=" + str(len(cookie_header)) if cookie_header else "NO"} '
                f'session_keys={list(session.keys())} '
                f'ua={request.headers.get("User-Agent", "")[:40]}'
            )
            return jsonify({
                'status': 'error',
                'message': '未登录，请重新登录',
                'code': 'unauthorized',
            }), 401
        return f(*args, **kwargs)
    return decorated


def safe_int(value, default=0, min_val=None, max_val=None):
    """安全整数转换，支持上下限约束"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    if min_val is not None:
        v = max(min_val, v)
    if max_val is not None:
        v = min(max_val, v)
    return v


def make_api(token=None):
    """
    创建 XiaoAiCourse API 实例。

    支持双模式:
      - token 为 str → 模式 A（Token 字符串）
      - session 中存储了 user_info → 模式 B（User Info Dict）

    Returns:
        XiaoAiCourse: API 客户端实例
    """
    t = token or get_auth_token()

    # 检查是否为 User Info Dict 模式
    user_info = session.get('user_info')
    if user_info and isinstance(user_info, dict):
        return XiaoAiCourse(user_info, session.get('debug_mode', False))

    # 默认 Token 字符串模式
    return XiaoAiCourse(t, session.get('debug_mode', False))


def make_api_for_token(token, user_info=None):
    """为后台线程创建 API 实例。

    后台线程没有 Flask 请求上下文，不能访问 session，
    因此认证信息必须在主线程取出后显式传入。

    Args:
        token: 认证 token 字符串
        user_info: 可选的 user_info 字典（模式 B 优先）

    Returns:
        XiaoAiCourse: API 客户端实例
    """
    debug_mode = bool(load_config().get('debug_mode'))
    if user_info and isinstance(user_info, dict):
        return XiaoAiCourse(user_info, debug_mode)
    return XiaoAiCourse(token, debug_mode)


# ─── Error Handlers ─────────────────────────────────────────────────
@app.errorhandler(400)
def bad_request(e):
    return jsonify({'status': 'error', 'message': '请求参数错误'}), 400

@app.errorhandler(404)
def not_found(e):
    return jsonify({'status': 'error', 'message': '接口不存在'}), 404

@app.errorhandler(500)
def internal_error(e):
    log.error(f'Internal error: {e}')
    return jsonify({'status': 'error', 'message': '服务器内部错误，请稍后重试'}), 500

@app.errorhandler(Exception)
def handle_exception(e):
    log.error(f'Unhandled exception: {e}', exc_info=True)
    return jsonify({'status': 'error', 'message': f'服务器异常: {str(e)}'}), 500


# ════════════════════════════════════════════════════════════════════════
#  Page Routes
# ════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    # Serve the Vite-built React (Appica UI) frontend from static/.
    return send_from_directory(os.path.join(app.root_path, 'static'), 'index.html')


# ════════════════════════════════════════════════════════════════════════
#  Config API
# ════════════════════════════════════════════════════════════════════════

@app.route('/api/config', methods=['GET'])
def get_config():
    cfg = load_config()
    cid, csecret = get_xiaomi_credentials()
    return jsonify({
        'phone': cfg.get('phone', ''),
        'has_password': bool(cfg.get('password')),
        'has_token': bool(cfg.get('saved_token')),
        'uuid': cfg.get('uuid', ''),
        'debug_mode': cfg.get('debug_mode', False),
        'has_local_kb': os.path.exists(LOCAL_KB_FILE),
        # ★ 新增: 是否存在 userinfo.txt
        'has_userinfo': os.path.exists(USERINFO_FILE),
        # ★ 新增: 应用凭证是否已配置（开源脱敏后不再硬编码）
        'has_app_credentials': bool(cid and csecret),
    })


# ════════════════════════════════════════════════════════════════════════
#  Login API — 原有密码登录 (保留)
# ════════════════════════════════════════════════════════════════════════

@app.route('/api/login', methods=['POST'])
def login():
    """小米账号密码登录（原有方式）"""
    data = request.get_json(silent=True) or {}
    phone = str(data.get('phone', '')).strip()
    password = str(data.get('password', ''))
    use_cache = data.get('use_cache', True)

    cfg = load_config()

    # Try cached token first
    if use_cache and cfg.get('saved_token'):
        token = cfg['saved_token']
        api = XiaoAiCourse(token, cfg.get('debug_mode', False))
        try:
            tables = api.get_tables()
        except Exception as e:
            log.warning(f'Cached token failed: {e}')
            tables = None
        if tables is not None:
            session['api_token'] = token
            session['debug_mode'] = cfg.get('debug_mode', False)
            log.info(f'Login via cached token for {cfg.get("phone", "?")}')
            return jsonify({'status': 'ok', 'message': '使用缓存Token登录成功', 'tables': tables})

    if not phone or not password:
        return jsonify({'status': 'error', 'message': '请输入手机号和密码'})

    device_uuid = cfg.get('uuid', str(uuid.uuid4()))
    if not cfg.get('uuid'):
        cfg['uuid'] = device_uuid
        save_config(cfg)

    try:
        auth = XiaomiAuthenticator(phone, password, device_uuid)
        tk, auth_err = auth.get_new_token()
    except Exception as e:
        log.error(f'Auth failed: {e}')
        return jsonify({'status': 'error', 'message': f'登录过程出错: {e}'})

    # Handle identity verification request from Xiaomi
    if auth.need_verification:
        ver_url = auth.notification_url
        log.warning(f'Identity verification needed for {phone}: {ver_url[:100]}')
        session['pending_login'] = {'phone': phone, 'password': password, 'device_uuid': device_uuid}
        return jsonify({
            'status': 'verification_needed',
            'message': '小米账号需要安全验证，系统将自动打开浏览器窗口完成验证',
            'verification_url': ver_url,
        })

    if tk:
        cfg['phone'] = phone
        cfg['saved_token'] = tk
        cfg['uuid'] = device_uuid
        save_config(cfg)

        api = XiaoAiCourse(tk, cfg.get('debug_mode', False))
        try:
            tables = api.get_tables()
        except Exception:
            tables = []
        session['api_token'] = tk
        session['debug_mode'] = cfg.get('debug_mode', False)

        log.info(f'Login success for {phone}')
        return jsonify({'status': 'ok', 'message': '登录成功', 'token': tk, 'token_preview': tk[:50] + '...', 'tables': tables})
    else:
        err_msg = auth_err or '未知错误'
        log.warning(f'Login failed for {phone}: {err_msg}')
        return jsonify({'status': 'error', 'message': f'登录失败: {err_msg}'})


@app.route('/api/login/verify-browser', methods=['POST'])
def login_verify_browser():
    """启动浏览器辅助安全验证"""
    pending = session.get('pending_login')
    if not pending:
        return jsonify({'status': 'error', 'message': '没有待验证的登录，请先尝试登录'})

    if login_verify_progress.get('status') == 'running':
        return jsonify({'status': 'error', 'message': '已有验证任务正在进行'})

    phone = pending['phone']
    password = pending['password']
    device_uuid = pending['device_uuid']
    cfg = load_config()

    def worker():
        _update_progress(login_verify_progress, status='running', message='正在启动...', token=None, tables=[])
        try:
            auth = XiaomiAuthenticator(phone, password, device_uuid)
            tk, auth_err = auth.get_new_token()
            if not auth.need_verification:
                _update_progress(login_verify_progress, status='error', message='验证状态已过期，请返回重新输入账号密码')
                return

            notification_url = auth.notification_url
            success, err = auth.browser_login(notification_url, login_verify_progress)
            if not success:
                _update_progress(login_verify_progress, status='error', message=err)
                return

            # Retry full OAuth flow with now-trusted session
            tk, auth_err = auth.get_new_token()
            if tk:
                cfg['phone'] = phone
                cfg['saved_token'] = tk
                cfg['uuid'] = device_uuid
                save_config(cfg)

                api = XiaoAiCourse(tk, cfg.get('debug_mode', False))
                try:
                    tables = api.get_tables()
                except Exception:
                    tables = []

                _update_progress(login_verify_progress, status='done', message='登录成功', token=tk, tables=tables)
            else:
                err_msg = auth_err or '验证后登录失败'
                if auth.need_verification:
                    err_msg = '验证似乎未完成，请确保在浏览器中完成了所有验证步骤'
                _update_progress(login_verify_progress, status='error', message=err_msg)
        except Exception as e:
            log.error(f'Verify browser error: {e}', exc_info=True)
            _update_progress(login_verify_progress, status='error', message=f'验证过程出错: {e}')

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return jsonify({'status': 'ok', 'message': '安全验证浏览器已启动'})


@app.route('/api/login/verify-status', methods=['GET'])
def login_verify_status():
    return jsonify(login_verify_progress)


@app.route('/api/login/verify-finalize', methods=['POST'])
def login_verify_finalize():
    """完成浏览器验证后的最终登录步骤"""
    if login_verify_progress.get('status') != 'done':
        return jsonify({'status': 'error', 'message': '验证尚未完成'})

    token = login_verify_progress.get('token')
    tables = login_verify_progress.get('tables', [])

    # 竞态防御: token 缺失说明 worker 尚未完成收尾，拒绝写入 None 凭证
    if not token:
        log.warning('Verify finalize: token missing (worker not finished), reject')
        return jsonify({'status': 'error', 'message': '登录收尾中，请稍候重试'})

    session['api_token'] = token
    session['debug_mode'] = load_config().get('debug_mode', False)
    session.pop('pending_login', None)
    _clear_progress(login_verify_progress)

    return jsonify({'status': 'ok', 'message': '登录成功', 'token': token, 'tables': tables})


# ════════════════════════════════════════════════════════════════════════
#  Login API — ★ userinfo.txt 直接导入 (新增)
# ════════════════════════════════════════════════════════════════════════

@app.route('/api/login/userinfo', methods=['POST'])
def login_via_userinfo():
    """★ 新增: 通过 userinfo.txt 文件直接导入登录。

    使用场景: 用户已通过抓包获取到 userinfo JSON，
              写入 userinfo.txt 后点击"导入登录"即可。
    """
    data = request.get_json(silent=True) or {}
    force_reload = data.get('force_reload', False)

    try:
        info = load_user_info()
    except FileNotFoundError as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'code': 'file_not_found',
        }), 404
    except ValueError as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'code': 'invalid_format',
        }), 400
    except Exception as e:
        log.error(f'Userinfo load failed: {e}')
        return jsonify({
            'status': 'error',
            'message': f'读取 userinfo.txt 失败: {e}',
        }), 500

    # 验证 Token 有效性
    api = XiaoAiCourse(info, load_config().get('debug_mode', False))
    try:
        tables = api.get_tables()
    except Exception as e:
        log.warning(f'Userinfo token validation failed: {e}')
        return jsonify({
            'status': 'error',
            'message': f'Token 验证失败，可能已过期或格式有误: {e}',
            'code': 'token_invalid',
        }), 401

    if tables is None:
        return jsonify({
            'status': 'error',
            'message': '云端返回空列表，Token 可能已失效，请重新抓包更新 userinfo.txt',
            'code': 'token_expired',
        }), 401

    # 登录成功，写入 session
    session['user_info'] = info
    session['api_token'] = info.get('authorization', '')
    session['debug_mode'] = load_config().get('debug_mode', False)

    log.info(f'Login via userinfo.txt successful')

    return jsonify({
        'status': 'ok',
        'message': 'userinfo.txt 导入成功',
        'auth_mode': 'userinfo',
        'token': info.get('authorization', ''),
        'tables': tables,
        'user_preview': {
            'userId': info.get('userId'),
            'deviceId': info.get('deviceId', '')[:8] + '...',
        },
    })


@app.route('/api/login/userinfo-status', methods=['GET'])
def get_userinfo_status():
    """检查 userinfo.txt 状态（前端用于显示按钮状态）"""
    result = {
        'exists': os.path.exists(USERINFO_FILE),
        'logged_in': session.get('user_info') is not None,
    }
    if result['exists']:
        try:
            with open(USERINFO_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            result['is_empty'] = len(content) == 0
            result['size'] = len(content)
        except Exception:
            result['is_empty'] = True
    return jsonify(result)


# ════════════════════════════════════════════════════════════════════════
#  QR Login API (保留不变)
# ════════════════════════════════════════════════════════════════════════

@app.route('/api/login/qr-start', methods=['POST'])
def login_qr_start():
    """启动扫码登录流程"""
    if qr_login_progress.get('status') == 'running':
        return jsonify({'status': 'error', 'message': '已有扫码登录任务正在进行'})

    def worker():
        _update_progress(qr_login_progress, status='running', message='正在启动...', token=None, tables=[])
        try:
            token, err = xiaoai_qr_login(qr_login_progress)
            if err:
                _update_progress(qr_login_progress, status='error', message=err)
                return

            api = XiaoAiCourse(token, load_config().get('debug_mode', False))
            try:
                tables = api.get_tables()
            except Exception:
                tables = []

            _update_progress(qr_login_progress, status='done', message='扫码登录成功', token=token, tables=tables)
        except Exception as e:
            log.error(f'QR login error: {e}', exc_info=True)
            _update_progress(qr_login_progress, status='error', message=f'扫码登录出错: {e}')

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return jsonify({'status': 'ok', 'message': '扫码登录已启动'})


@app.route('/api/login/qr-status', methods=['GET'])
def login_qr_status():
    return jsonify(qr_login_progress)


@app.route('/api/login/qr-finalize', methods=['POST'])
def login_qr_finalize():
    """完成扫码登录"""
    if qr_login_progress.get('status') != 'done':
        return jsonify({'status': 'error', 'message': '扫码尚未完成'})

    token = qr_login_progress.get('token')
    tables = qr_login_progress.get('tables', [])

    # 竞态防御: token 缺失说明 worker 尚未完成收尾，拒绝写入 None 凭证
    if not token:
        log.warning('QR finalize: token missing (worker not finished), reject')
        return jsonify({'status': 'error', 'message': '扫码收尾中，请稍候重试'})

    session['api_token'] = token
    session['debug_mode'] = load_config().get('debug_mode', False)

    # Save token for future sessions
    cfg = load_config()
    cfg['saved_token'] = token
    save_config(cfg)

    _clear_progress(qr_login_progress)

    return jsonify({'status': 'ok', 'message': '扫码登录成功', 'token': token, 'tables': tables})


# ════════════════════════════════════════════════════════════════════════
#  Tables API
# ════════════════════════════════════════════════════════════════════════

@app.route('/api/tables', methods=['GET'])
@require_login
def get_tables():
    api = make_api()
    try:
        tables = api.get_tables()
    except Exception as e:
        log.error(f'Get tables failed: {e}')
        return jsonify({'status': 'error', 'message': f'获取课表列表失败: {e}'})
    if tables is None:
        return jsonify({
            'status': 'error',
            'message': '登录凭证已失效，请重新登录',
            'code': 'unauthorized',
        }), 401
    return jsonify({'status': 'ok', 'tables': tables})


@app.route('/api/table/<tid>', methods=['GET'])
@require_login
def get_table_detail(tid):
    if not tid:
        return jsonify({'status': 'error', 'message': '缺少课表ID'}), 400
    api = make_api()
    try:
        detail = api.get_detail(tid)
    except Exception as e:
        log.error(f'Get table detail failed: {e}')
        return jsonify({'status': 'error', 'message': f'获取课表详情失败: {e}'})
    return jsonify({'status': 'ok', 'detail': detail})


@app.route('/api/table/create', methods=['POST'])
@require_login
def create_table():
    data = request.get_json(silent=True) or {}
    name = str(data.get('name', '新课表')).strip()
    if not name:
        return jsonify({'status': 'error', 'message': '课表名称不能为空'}), 400
    api = make_api()
    try:
        api.create_t(name)
        log.info(f'Table created: {name}')
    except Exception as e:
        log.error(f'Create table failed: {e}')
        return jsonify({'status': 'error', 'message': f'创建课表失败: {e}'})
    return jsonify({'status': 'ok', 'message': f'课表 "{name}" 创建成功'})


@app.route('/api/table/delete', methods=['POST'])
@require_login
def delete_table():
    data = request.get_json(silent=True) or {}
    tid = data.get('tid')
    if not tid:
        return jsonify({'status': 'error', 'message': '缺少课表ID'}), 400
    api = make_api()
    try:
        detail = api.get_detail(tid)
        sid = ''
        setting = None
        if detail:
            setting = detail.get('setting')
            if isinstance(setting, dict):
                sid = data.get('sid') or setting.get('id') or ''
        log.info(f'Deleting table: tid={tid}, sid={sid}, setting_keys={list(setting.keys()) if isinstance(setting, dict) else "N/A"}')
        api.del_t(tid, sid)
        log.info(f'Table deleted: {tid}')
    except Exception as e:
        log.error(f'Delete table failed: {e}')
        return jsonify({'status': 'error', 'message': f'删除课表失败: {e}'})
    return jsonify({'status': 'ok', 'message': '课表已删除'})


# ════════════════════════════════════════════════════════════════════════
#  Sync API
# ════════════════════════════════════════════════════════════════════════

@app.route('/api/sync/preview', methods=['GET', 'POST'])
@require_login
def sync_preview():
    """★ 同步预览：告诉用户"要同步到哪个课表"以及"会改什么"，再决定是否执行。

    用户痛点：点同步前不知道传到哪、会动多少数据，只能盲点。

    Query / Body:
        tid: 目标课表 ID（可选；不传则用"当前课表"，再退化为第一个）

    Returns:
        {
          status, available,
          target: {id, name, isCurrent, courseCount},
          source: {type:'local', courseCount},
          plan: {
            toAdd: [{name, teacher, position, day, sections, weeks}],
            toDelete: [{name, teacher, position, day, sections, weeks}],
            unchanged: int
          },
          stats: {add, delete, unchanged},
          warnings: [str]
        }
    """
    data = request.get_json(silent=True) or {}
    tid = data.get('tid') or request.args.get('tid')

    api = make_api()

    # ── 1. 取课表列表，确定目标课表 ──
    try:
        tables = api.get_tables()
    except Exception as e:
        log.error(f'Preview: 获取课表列表失败: {e}')
        return jsonify({'status': 'error', 'message': f'获取课表列表失败: {e}'})

    if tables is None:
        return jsonify({
            'status': 'error',
            'message': '登录凭证已失效，请重新登录',
            'code': 'unauthorized',
        }), 401

    if not tables:
        return jsonify({
            'status': 'ok', 'available': False,
            'message': '云端还没有课表，请先点「+ 新建」创建',
        })

    target = None
    if tid:
        target = next((t for t in tables if str(t.get('id')) == str(tid)), None)
    if not target:
        target = next((t for t in tables if t.get('current') == 1), None)
    if not target:
        target = tables[0]

    warnings = []
    if tid and str(target.get('id')) != str(tid):
        warnings.append(f'指定的课表不存在，已改为「{target.get("name")}」')

    # ── 2. 取目标课表的云端课程 ──
    try:
        detail = api.get_detail(target['id'])
    except Exception as e:
        log.error(f'Preview: 获取课表详情失败: {e}')
        return jsonify({'status': 'error', 'message': f'获取课表详情失败: {e}'})

    if not detail:
        return jsonify({'status': 'error', 'message': '获取课表详情失败'})

    remote_courses = detail.get('courses', [])

    # ── 3. 取本地捕获的课表 ──
    local_courses = []
    if os.path.exists(LOCAL_KB_FILE):
        try:
            with open(LOCAL_KB_FILE, 'r', encoding='utf-8') as f:
                local_courses = QiangZhiParser.parse_html(f.read())
        except Exception as e:
            log.warning(f'Preview: 本地课表解析失败: {e}')

    if not local_courses:
        return jsonify({
            'status': 'ok', 'available': False,
            'target': {
                'id': target.get('id'),
                'name': target.get('name', ''),
                'isCurrent': target.get('current') == 1,
                'courseCount': len(remote_courses),
            },
            'message': '还没有本地课表，请先点「重新捕获课表」',
        })

    # ── 4. 算差异（与同步逻辑用同一套指纹）──
    def fp(c):
        return f'{c.get("name")}|{c.get("position")}|{c.get("teacher")}|{c.get("day")}|{c.get("sections")}|{c.get("weeks")}'

    remote_map = {fp(rc): rc for rc in remote_courses}
    local_fps = set(fp(lc) for lc in local_courses)

    to_add = [lc for lc in local_courses if fp(lc) not in remote_map]
    to_delete = [rc for rc in remote_courses if fp(rc) not in local_fps]
    unchanged = sum(1 for lc in local_courses if fp(lc) in remote_map)

    def slim(c, cap=3):
        """压缩 weeks 展示（如 '1,2,3,...,10' → '1,2,3,...'）"""
        weeks = str(c.get('weeks') or '')
        if len(weeks) > 18:
            weeks = weeks[:15] + '...'
        return {
            'name': c.get('name', ''),
            'teacher': c.get('teacher', ''),
            'position': c.get('position', ''),
            'day': c.get('day'),
            'sections': str(c.get('sections') or ''),
            'weeks': weeks,
        }

    # 差异过大时给出提醒（可能是选错课表 / 学期不匹配）
    total = len(to_add) + len(to_delete)
    if len(to_delete) > 0 and len(to_delete) >= max(5, len(remote_courses) * 0.8):
        warnings.append(
            f'将删除目标课表里 {len(to_delete)}/{len(remote_courses)} 门课程，'
            f'请确认没有选错课表'
        )

    return jsonify({
        'status': 'ok',
        'available': True,
        'target': {
            'id': target.get('id'),
            'name': target.get('name', ''),
            'isCurrent': target.get('current') == 1,
            'courseCount': len(remote_courses),
        },
        'source': {
            'type': 'local',
            'courseCount': len(local_courses),
        },
        'plan': {
            'toAdd': [slim(c) for c in to_add[:50]],
            'toDelete': [slim(c) for c in to_delete[:50]],
            'unchanged': unchanged,
            'addTruncated': len(to_add) > 50,
            'deleteTruncated': len(to_delete) > 50,
        },
        'stats': {
            'add': len(to_add),
            'delete': len(to_delete),
            'unchanged': unchanged,
            'operations': total,
        },
        'warnings': warnings,
    })


@app.route('/api/sync', methods=['POST'])
@require_login
def sync():
    """启动异步同步任务，立即返回 taskId，前端轮询 /api/sync/progress 获取进度。

    改造点（性能）：
      - 原来是同步阻塞式，逐门课程串行发 HTTP，20 门课最坏要几分钟且无任何反馈
      - 现在改为后台线程 + 批量并行（默认 6 并发）+ 连接池复用
    """
    data = request.get_json(silent=True) or {}
    tid = data.get('tid')
    params = data.get('params', {})

    if not tid:
        return jsonify({'status': 'error', 'message': '请选择要同步的课表'}), 400

    if sync_progress.get('status') == 'running':
        return jsonify({'status': 'error', 'message': '已有同步任务正在运行，请等待完成'}), 409

    # ── 前置校验：课表存在性 + 本地课表文件 ──
    api = make_api()
    try:
        cfg = api.get_detail(tid)
        if not cfg:
            return jsonify({'status': 'error', 'message': '获取课表详情失败'})

        if not os.path.exists(LOCAL_KB_FILE):
            return jsonify({'status': 'error', 'message': '本地课表文件不存在，请先捕获课表'})

        with open(LOCAL_KB_FILE, 'r', encoding='utf-8') as f:
            local_list = QiangZhiParser.parse_html(f.read())

        if not local_list:
            log.warning(f'本地课表文件存在但解析结果为空 (文件大小: {os.path.getsize(LOCAL_KB_FILE)} bytes)')
            return jsonify({'status': 'error', 'message': '本地课表文件中没有课程数据（表格为空），请重新捕获课表并在教务系统中打开课表页面后再关闭浏览器'})
    except Exception as e:
        log.error(f'Sync preparation failed: {e}')
        return jsonify({'status': 'error', 'message': f'同步准备失败: {e}'})

    # ── 参数校验与清洗 ──
    season = params.get('season', 'winter')
    if season not in ('winter', 'summer'):
        season = 'winter'

    start_semester = params.get('startSemester', '2026-03-02')
    present_week = safe_int(params.get('presentWeek', 1), default=1, min_val=1, max_val=30)
    total_week = safe_int(params.get('totalWeek', 20), default=20, min_val=1, max_val=30)
    m_num = safe_int(params.get('m_num', 4), default=4, min_val=0, max_val=10)
    a_num = safe_int(params.get('a_num', 4), default=4, min_val=0, max_val=10)
    n_num = safe_int(params.get('n_num', 2), default=2, min_val=0, max_val=10)

    # ★ 需求①：前端微调后的节次时间（可选）。缺失/非法时回退到季节预设
    custom_sections = params.get('sections')
    tt = generate_time_table(season, custom_sections)

    # ★ 需求②：可选的课表重命名（学期名，如 26-27-1）
    new_name = (params.get('tableName') or '').strip() or None

    task_id = uuid.uuid4().hex
    token = get_auth_token()
    user_info = session.get('user_info')

    _clear_progress(sync_progress)
    _update_progress(
        sync_progress,
        status='running', task_id=task_id, phase='preparing',
        message='正在准备同步...', done=0, total=len(local_list),
        started_at=time.time(), result=None,
    )

    def sync_worker():
        """后台同步线程：批量并行增删 + 分阶段进度上报。"""
        try:
            worker_api = make_api_for_token(token, user_info)
            # 重新取一次详情，避免主线程的 cfg 被后续修改
            detail = worker_api.get_detail(tid) or cfg

            _update_progress(sync_progress, phase='sections',
                             message='正在同步作息时间设置...', done=0, total=len(local_list))

            worker_api.sync_settings(
                detail,
                name=new_name,
                startSemester=start_semester,
                presentWeek=present_week,
                totalWeek=total_week,
                m_num=m_num, a_num=a_num, n_num=n_num,
                sections_data=tt,
            )

            palette = [
                {'background': '#E5F4FF', 'color': '#00A6F2'},
                {'background': '#FDEBDE', 'color': '#FF8F00'},
                {'background': '#DEFBFA', 'color': '#00BFA5'},
                {'background': '#EDEDFF', 'color': '#536DFE'},
                {'background': '#FCEBCD', 'color': '#F57F17'},
                {'background': '#FFEFF0', 'color': '#FF5252'},
                {'background': '#EAF1FF', 'color': '#448AFF'},
                {'background': '#FFEDF8', 'color': '#FF4081'},
            ]

            colors = {}
            unique_names = sorted(set(c['name'] for c in local_list))
            for i, name in enumerate(unique_names):
                colors[name] = palette[i % len(palette)]

            def fp(c):
                return f'{c["name"]}|{c["position"]}|{c["teacher"]}|{c["day"]}|{c["sections"]}|{c["weeks"]}'

            rem_fps = {fp(rc): rc['id'] for rc in detail.get('courses', [])}

            # ── 阶段 1：计算差异 ──
            local_fps = set(fp(lc) for lc in local_list)
            to_delete = [(f_str, rid) for f_str, rid in rem_fps.items()
                         if f_str not in local_fps]
            to_add = [lc for lc in local_list if fp(lc) not in rem_fps]
            skipped = [lc['name'] for lc in local_list if fp(lc) in rem_fps]

            total_ops = len(to_delete) + len(to_add)
            _update_progress(sync_progress, total=total_ops,
                             message=f'发现 {len(to_add)} 门新增 · {len(to_delete)} 门待删除')

            if total_ops == 0:
                _update_progress(
                    sync_progress, status='done', phase='finished',
                    message='课表已是最新，无需改动', done=0, total=0,
                    result={
                        'status': 'ok',
                        'message': '同步完成（无变更）',
                        'stats': {'added': 0, 'deleted': 0, 'skipped': len(set(skipped))},
                    },
                )
                return

            # ── 阶段 2：并行删除 ──
            deleted, delete_failed = [], []
            if to_delete:
                _update_progress(sync_progress, phase='deleting',
                                 message=f'正在删除 {len(to_delete)} 门过期课程...',
                                 done=0, total=len(to_delete))

                def on_del(done, total):
                    _update_progress(sync_progress, done=done, total=total,
                                     message=f'删除过期课程 {done}/{total}')

                del_ids = [rid for _, rid in to_delete]
                ok_ids, fail_ids = worker_api.batch_delete_courses(
                    tid, del_ids, max_workers=6, on_progress=on_del)
                ok_set = set(ok_ids)
                for f_str, rid in to_delete:
                    (deleted if rid in ok_set else delete_failed).append(f_str)

            # ── 阶段 3：并行新增 ──
            added, add_failed = [], []
            if to_add:
                _update_progress(sync_progress, phase='adding',
                                 message=f'正在上传 {len(to_add)} 门课程...',
                                 done=0, total=len(to_add))

                def on_add(done, total):
                    _update_progress(sync_progress, done=done, total=total,
                                     message=f'上传课程 {done}/{total}')

                payload = [(lc, colors[lc['name']]) for lc in to_add]
                ok_courses, fail_courses = worker_api.batch_add_courses(
                    tid, payload, max_workers=6, on_progress=on_add)
                added = [c['name'] for c in ok_courses]
                add_failed = [c['name'] for c in fail_courses]

            log.info(f'Sync completed: +{len(set(added))} -{len(deleted)} ={len(set(skipped))}')

            result = {
                'status': 'ok',
                'message': '同步完成',
                'stats': {
                    'added': len(set(added)),
                    'deleted': len(deleted),
                    'skipped': len(set(skipped)),
                },
            }
            if delete_failed:
                result['delete_failed'] = delete_failed
                result['message'] += f' ({len(delete_failed)} 门课程删除失败)'
                log.warning(f'Course deletion failed: {delete_failed}')
            if add_failed:
                result['add_failed'] = add_failed
                result['message'] += f' ({len(add_failed)} 门课程添加失败)'
                log.warning(f'Course addition failed: {add_failed}')

            _update_progress(
                sync_progress, status='done', phase='finished',
                message=result['message'],
                done=total_ops, total=total_ops, result=result,
                finished_at=time.time(),
            )
        except Exception as e:
            log.error(f'Sync failed: {e}', exc_info=True)
            _update_progress(
                sync_progress, status='error', phase='failed',
                message=f'同步过程出错: {e}',
                result={'status': 'error', 'message': f'同步过程出错: {e}'},
                finished_at=time.time(),
            )

    threading.Thread(target=sync_worker, daemon=True, name='sync-worker').start()

    return jsonify({
        'status': 'ok',
        'async': True,
        'taskId': task_id,
        'message': '同步任务已启动',
        'sections': tt,
        'sectionCount': len(tt),
    })


@app.route('/api/sync/progress', methods=['GET'])
def get_sync_progress():
    """轮询同步任务进度。"""
    with _progress_lock:
        snapshot = dict(sync_progress)
    return jsonify({'status': 'ok', 'progress': snapshot})


@app.route('/api/schedule/rules', methods=['GET', 'POST'])
def schedule_rules():
    """作息规则读写。

    GET  → 返回当前生效的规则（单节时长/课间/节数/各段起点）
    POST → 用 body 里的参数覆盖规则，并返回重算后的预设节次表

    Body 字段（全部可选）：
        slotDuration, innerBreak, longBreak, groupSize,
        winterAfternoonStart, winterEveningStart,
        summerAfternoonStart, summerEveningStart
    """
    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        rules = apply_rule_overrides(body)
        log.info(f'Schedule rules updated: {body}')
    else:
        rules = {k: dict(v) for k, v in SCHEDULE_RULES.items()}

    # 注意：这些常量会被 apply_rule_overrides 就地改写，
    # 所以必须在这里重新读取模块属性，不能依赖 import 时捕获的值。
    import core as _core
    return jsonify({
        'status': 'ok',
        'rules': rules,
        'presets': SCHEDULE_PRESETS,
        'meta': {
            'slotDuration': _core.SLOT_DURATION,
            'innerBreak': _core.INNER_BREAK,
            'longBreak': _core.LONG_BREAK,
            'groupSize': _core.GROUP_SIZE,
            'defaultTotalSections': _core.DEFAULT_TOTAL_SECTIONS,
        },
    })


@app.route('/api/semesters', methods=['GET'])
def get_semesters():
    """返回按学年推算的学期名称预设（26-27-1 / 26-27-2 等）。"""
    now = datetime.now()
    return jsonify({
        'status': 'ok',
        'current': semester_name(current_semester_year(now), 1 if now.month >= 9 else 2),
        'terms': suggest_semester_terms(now),
    })


@app.route('/api/schedule/suggest', methods=['GET'])
@require_login
def suggest_schedule():
    """★ 智能作息推荐：用本地课表真实占用的节次反推作息时间表。

    Query:
        season: 'winter' | 'summer'（作为兜底与节数参考）

    Returns:
        {
          status, source: 'local'|'remote'|'none',
          sections: [{'i','s','e'}, ...],
          reason, usedSections, maxUsed, groups, periods
        }
    """
    season = request.args.get('season', 'winter')
    if season not in ('winter', 'summer'):
        season = 'winter'

    # 允许带上规则参数（前端"保存规则"后可即时生效）
    overrides = {}
    for k in ('slotDuration', 'innerBreak', 'longBreak', 'groupSize'):
        v = request.args.get(k)
        if v:
            overrides[k] = v
    for k in ('winterAfternoonStart', 'winterEveningStart',
              'summerAfternoonStart', 'summerEveningStart'):
        v = request.args.get(k)
        if v:
            overrides[k] = v
    if overrides:
        apply_rule_overrides(overrides)

    courses = []
    source = 'none'

    # 优先用本地捕获的课表（最快、免登录态依赖）
    if os.path.exists(LOCAL_KB_FILE):
        try:
            with open(LOCAL_KB_FILE, 'r', encoding='utf-8') as f:
                courses = QiangZhiParser.parse_html(f.read())
            if courses:
                source = 'local'
        except Exception as e:
            log.warning(f'Suggest: 本地课表解析失败: {e}')

    # 本地没有则回退到云端已选课表
    if not courses:
        tid = request.args.get('tid')
        try:
            api = make_api()
            if not tid:
                tables = api.get_tables() or []
                cur = next((t for t in tables if t.get('current') == 1), None)
                tid = (cur or (tables[0] if tables else {})).get('id')
            if tid:
                detail = api.get_detail(tid)
                if detail:
                    courses = detail.get('courses', [])
                    if courses:
                        source = 'remote'
        except Exception as e:
            log.warning(f'Suggest: 云端课表回退失败: {e}')

    result = infer_sections_from_courses(courses, season)

    if not result:
        return jsonify({
            'status': 'ok',
            'source': source,
            'available': False,
            'message': '暂无可用于推荐的课表数据，请先捕获课表',
            'sections': None,
        })

    return jsonify({
        'status': 'ok',
        'source': source,
        'available': True,
        'sections': result['sections'],
        'reason': result['reason'],
        'usedSections': result['usedSections'],
        'maxUsed': result['maxUsed'],
        'groups': result['groups'],
        'periods': result['periods'],
        'groupSize': result['groupSize'],
        'season': season,
    })


# ════════════════════════════════════════════════════════════════════════
#  Capture API
# ════════════════════════════════════════════════════════════════════════

@app.route('/api/capture/start', methods=['POST'])
@require_login
def start_capture():
    if capture_progress.get('status') == 'running':
        return jsonify({'status': 'error', 'message': '已有捕获任务正在运行'})

    def capture_worker():
        _update_progress(capture_progress, status='running', count=0,
                         stop_requested=False, message='正在启动浏览器...')

        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                _update_progress(capture_progress, message='正在打开浏览器，请在弹出的浏览器窗口中登录教务系统...')

                browser = None
                for launcher in [
                    lambda: p.chromium.launch(headless=False, channel='chrome'),
                    lambda: p.chromium.launch(headless=False, channel='msedge'),
                    lambda: p.firefox.launch(headless=False),
                ]:
                    try:
                        browser = launcher()
                        break
                    except Exception:
                        continue

                if browser is None:
                    _update_progress(capture_progress, status='error', message='无法启动浏览器，请确认已安装 Chrome/Edge/Firefox')
                    return

                context = browser.new_context()
                page = context.new_page()

                def handle(resp):
                    if page.is_closed():
                        return
                    if 'xskb_list.do' in resp.url and resp.status == 200:
                        try:
                            content = resp.text()
                            # 验证: 必须包含 timetable 表格且表格中有实际课程数据
                            if 'timetable' in content and 'kbcontent' in content:
                                # 快速校验: 检查是否包含实际课程文本（非空表格）
                                # 用 BeautifulSoup 确认表格中有课程内容
                                from bs4 import BeautifulSoup
                                soup = BeautifulSoup(content, 'html.parser')
                                table = soup.find('table', id='timetable')
                                if table:
                                    kb_divs = table.find_all('div', class_=lambda c: c and c.startswith('kbcontent'))
                                    has_data = any(
                                        d.get_text(strip=True) and 'nbsp' not in d.get_text()
                                        for d in kb_divs
                                    )
                                    if not has_data:
                                        log.warning('Captured timetable has no course data, skipping save')
                                        return
                                with open(LOCAL_KB_FILE, 'w', encoding='utf-8') as f:
                                    f.write(content)
                                with _progress_lock:
                                    capture_progress['count'] += 1
                                    count = capture_progress['count']
                                _update_progress(capture_progress,
                                    message=f'已捕获 {count} 次 | {time.strftime("%H:%M:%S")}')
                        except Exception as e:
                            log.warning(f'Capture response handler error: {e}')

                page.on('response', handle)
                page.goto('https://kdjw.hnust.edu.cn/')
                _update_progress(capture_progress,
                    message='页面已加载，请登录并打开课表页面，捕获成功后会自动完成...')

                # 自动收尾逻辑（解决流程卡死）:
                #   1. 捕获到有效数据后 8 秒无新增 → 自动关闭浏览器完成
                #   2. 前端调用 /api/capture/stop → 立即停止
                #   3. 10 分钟无任何有效捕获 → 超时报错
                #   4. 用户手动关闭浏览器窗口 → 立即收尾
                AUTO_CLOSE_AFTER_CAPTURE = 8
                NO_CAPTURE_TIMEOUT = 600
                start_ts = time.time()
                last_capture_ts = None
                prev_count = 0
                stopped = False
                while not page.is_closed():
                    try:
                        page.wait_for_timeout(500)
                    except Exception:
                        break
                    with _progress_lock:
                        count_now = capture_progress.get('count', 0)
                        stop_requested = capture_progress.get('stop_requested', False)
                    if stop_requested:
                        stopped = True
                        break
                    if count_now > prev_count:
                        prev_count = count_now
                        last_capture_ts = time.time()
                        if count_now == 1:
                            _update_progress(capture_progress,
                                message=f'已捕获到课表数据！{AUTO_CLOSE_AFTER_CAPTURE} 秒后自动完成（切换其他课表视图可继续捕获）...')
                    elif (
                        count_now > 0
                        and last_capture_ts is not None
                        and time.time() - last_capture_ts >= AUTO_CLOSE_AFTER_CAPTURE
                    ):
                        break
                    elif count_now == 0 and time.time() - start_ts >= NO_CAPTURE_TIMEOUT:
                        break

                if not page.is_closed():
                    try:
                        page.close()
                    except Exception:
                        pass
                browser.close()

                if stopped:
                    with _progress_lock:
                        final_count = capture_progress.get('count', 0)
                    if final_count > 0:
                        _update_progress(capture_progress, status='done',
                            message=f'捕获已停止，共获取 {final_count} 次课表数据')
                    else:
                        _update_progress(capture_progress, status='error',
                            message='捕获已手动停止，未捕获到课表数据')
                elif prev_count > 0:
                    _update_progress(capture_progress, status='done',
                        message=f'捕获完成！共获取 {prev_count} 次课表数据')
                    log.info(f'Capture completed: {prev_count} captures')
                else:
                    if time.time() - start_ts >= NO_CAPTURE_TIMEOUT:
                        _update_progress(capture_progress, status='error',
                            message='等待超时（10 分钟）：未捕获到课表数据，请确认已登录教务系统并打开了课表页面')
                    else:
                        _update_progress(capture_progress, status='error',
                            message='未捕获到课表数据，请确认是否已正确登录并打开了课表页面')
        except Exception as e:
            _update_progress(capture_progress, status='error', message=f'捕获过程出错: {e}')
            log.error(f'Capture error: {e}', exc_info=True)

    thread = threading.Thread(target=capture_worker, daemon=True)
    thread.start()

    return jsonify({'status': 'ok', 'message': '开始捕获课表数据'})


@app.route('/api/capture/status', methods=['GET'])
def get_capture_status():
    return jsonify(capture_progress)


@app.route('/api/capture/stop', methods=['POST'])
def stop_capture():
    """手动停止正在运行的捕获任务"""
    if capture_progress.get('status') != 'running':
        return jsonify({'status': 'error', 'message': '没有正在运行的捕获任务'})
    _update_progress(capture_progress, stop_requested=True)
    return jsonify({'status': 'ok', 'message': '已请求停止捕获'})


# ════════════════════════════════════════════════════════════════════════
#  Diagnostic API
# ═══════════ Desktop ════════════════════════════════════════════════════

@app.route('/api/diagnose', methods=['GET'])
def diagnose():
    """Connectivity & auth diagnostic endpoint."""
    results = {}

    # Test 1: Xiaomi account endpoint
    try:
        r = requests.get(
            'https://account.xiaomi.com/pass/serviceLogin?_json=true&sid=ai-service',
            timeout=10,
        )
        results['xiaomi_account'] = {'ok': True, 'status': r.status_code, 'code': None}
        try:
            d = json.loads(r.text.replace('&&&START&&&', ''))
            results['xiaomi_account']['code'] = d.get('code')
        except Exception:
            pass
    except Exception as e:
        results['xiaomi_account'] = {'ok': False, 'error': str(e)}

    # Test 2: AI service endpoint (new domain)
    try:
        r = requests.get('https://i.xiaomixiaoai.com/course-multi-auth/tables', timeout=10)
        results['ai_service'] = {'ok': True, 'status': r.status_code}
    except Exception as e:
        results['ai_service'] = {'ok': False, 'error': str(e)}

    # Test 3: DNS resolution
    try:
        import socket
        ip = socket.gethostbyname('account.xiaomi.com')
        results['dns'] = {'ok': True, 'ip': ip}
    except Exception as e:
        results['dns'] = {'ok': False, 'error': str(e)}

    # Test 4: Saved config status
    cfg = load_config()
    results['config'] = {
        'has_saved_phone': bool(cfg.get('phone')),
        'has_saved_token': bool(cfg.get('saved_token')),
        'has_uuid': bool(cfg.get('uuid')),
    }

    results['session'] = {'has_token': bool(session.get('api_token'))}
    results['userinfo_file'] = {'exists': os.path.exists(USERINFO_FILE)}

    overall = all(v.get('ok', True) for v in results.values() if isinstance(v, dict))
    results['overall'] = 'ok' if overall else 'issues_found'

    return jsonify(results)


# ════════════════════════════════════════════════════════════════════════
#  Utility APIs
# ════════════════════════════════════════════════════════════════════════

@app.route('/api/debug-mode', methods=['POST'])
@require_login
def toggle_debug():
    data = request.get_json(silent=True) or {}
    debug_mode = bool(data.get('debug_mode', False))
    session['debug_mode'] = debug_mode
    cfg = load_config()
    cfg['debug_mode'] = debug_mode
    save_config(cfg)
    return jsonify({'status': 'ok', 'debug_mode': debug_mode})


@app.route('/api/courses/local', methods=['GET'])
@require_login
def get_local_courses():
    if not os.path.exists(LOCAL_KB_FILE):
        return jsonify({'status': 'error', 'message': '无本地课表文件，请先捕获课表'})
    try:
        with open(LOCAL_KB_FILE, 'r', encoding='utf-8') as f:
            courses = QiangZhiParser.parse_html(f.read())
        return jsonify({'status': 'ok', 'courses': courses, 'count': len(courses)})
    except Exception as e:
        log.error(f'Local courses read failed: {e}')
        return jsonify({'status': 'error', 'message': f'读取本地课表失败: {e}'})


@app.route('/api/schedules', methods=['GET'])
def get_schedules():
    """返回作息时间预设（节次可逐节微调）+ 学期名称预设。"""
    now = datetime.now()
    return jsonify({
        'status': 'ok',
        'presets': SCHEDULE_PRESETS,
        'semesters': {
            'current': semester_name(current_semester_year(now), 1 if now.month >= 9 else 2),
            'terms': suggest_semester_terms(now),
        },
    })


@app.route('/api/export', methods=['GET'])
@require_login
def export_data():
    """Export course data in JSON or Excel format."""
    fmt = request.args.get('format', 'json')
    tid = request.args.get('tid')

    if not tid:
        return jsonify({'status': 'error', 'message': '请指定课表ID'}), 400

    api = make_api()
    try:
        detail = api.get_detail(tid)
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'获取课表失败: {e}'})

    if not detail:
        return jsonify({'status': 'error', 'message': '课表不存在'})

    courses = detail.get('courses', [])

    if fmt == 'json':
        return jsonify({
            'status': 'ok',
            'name': detail.get('name', ''),
            'courses': courses,
        })
    elif fmt == 'excel':
        try:
            import io
            from openpyxl import Workbook
            from openpyxl.styles import Font, Alignment, PatternFill

            wb = Workbook()
            ws = wb.active
            ws.title = detail.get('name', '课表')[:31]

            headers = ['课程名', '教师', '教室', '星期', '节次', '周次']
            ws.append(headers)
            for cell in ws[1]:
                cell.font = Font(bold=True, size=12, color='FFFFFF')
                cell.fill = PatternFill(start_color='0071E3', end_color='0071E3', fill_type='solid')
                cell.alignment = Alignment(horizontal='center')

            day_map = {1: '周一', 2: '周二', 3: '周三', 4: '周四', 5: '周五', 6: '周六', 7: '周日'}
            for c in courses:
                ws.append([
                    c.get('name', ''),
                    c.get('teacher', ''),
                    c.get('position', ''),
                    day_map.get(c.get('day', 0), f"周{c.get('day', '?')}"),
                    c.get('sections', ''),
                    c.get('weeks', ''),
                ])

            # Auto column width
            for col in ws.columns:
                max_len = 0
                for cell in col:
                    if cell.value:
                        max_len = max(max_len, len(str(cell.value)))
                ws.column_dimensions[col[0].column_letter].width = max(max_len + 4, 10)

            buf = io.BytesIO()
            wb.save(buf)
            buf.seek(0)

            from flask import send_file
            return send_file(
                buf,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name=f'{detail.get("name", "课表")}_{datetime.now().strftime("%Y%m%d")}.xlsx',
            )
        except ImportError:
            return jsonify({'status': 'error', 'message': '缺少 openpyxl 依赖，请运行 pip install openpyxl'}), 500
        except Exception as e:
            log.error(f'Excel export failed: {e}')
            return jsonify({'status': 'error', 'message': f'Excel 导出失败: {e}'}), 500
    else:
        return jsonify({'status': 'error', 'message': f'不支持的导出格式: {fmt}'}), 400


# ═════════════Desktop ════════════════════════════════════════════════════
#  Run
# ════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    log.info('=' * 50)
    log.info('小爱课程表教务助手启动中...')
    log.info(f'数据目录: {DATA_DIR}')
    log.info(f'访问地址: http://127.0.0.1:8080')
    log.info(f'认证模式: 支持 密码登录 / 扫码登录 / userinfo.txt 导入')
    log.info('=' * 50)
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='127.0.0.1', port=8080, debug=debug_mode)
