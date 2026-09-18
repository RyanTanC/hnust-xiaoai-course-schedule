# -*- coding: utf-8 -*-
"""
小爱课程表 · HNUST 教务助手 — 核心业务逻辑模块

模块说明:
  - XiaomiAuthenticator: 小米账号密码登录（OAuth2 完整流程）
  - XiaoAiCourse:        小爱课表云端 API 客户端（支持双模式认证）
  - QiangZhiParser:      教务系统 HTML 课表解析器
  - HNUSTCapturer:       Playwright 浏览器捕获器
  - xiaoai_qr_login:     小米扫码登录函数
  - load_user_info:      userinfo.txt 直读 Token 模式

认证模式说明:
  1. Token 字符串模式 (旧/兼容):  XiaoAiCourse("AO-TOKEN-V1 ...")
  2. User Info Dict 模式 (新):    XiaoAiCourse({"authorization": "...", "userAgent": "..."})
  3. userinfo.txt 文件模式:        load_user_info() → 返回 user_info dict

作者: dezige | 维护: Senior Developer
"""

import os
import sys
import json
import time
import re
import uuid
import base64
import hashlib
import logging
import threading
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup

log = logging.getLogger('aischedule')


# ─── 路径工具 ───────────────────────────────────────────────────────────

def get_resource_path(relative_path):
    """获取资源路径（兼容 PyInstaller 打包环境）"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


# ─── 全局路径常量 ────────────────────────────────────────────────────────

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
CONFIG_FILE = os.path.join(DATA_DIR, 'aischedule_config.json')
LOCAL_KB_FILE = os.path.join(DATA_DIR, 'xskb_list.do.txt')
USERINFO_FILE = os.path.join(DATA_DIR, 'userinfo.txt')

os.makedirs(DATA_DIR, exist_ok=True)


# ════════════════════════════════════════════════════════════════════════
#  Part 0: 环境变量加载（开源脱敏）
# ════════════════════════════════════════════════════════════════════════

def load_dotenv(path=None, override=False):
    """极简 .env 加载器（不引入第三方依赖）。

    读取 KEY=VALUE 格式，跳过空行与 # 注释，支持两侧引号剥离。
    已存在的环境变量默认不覆盖（override=False），保证
    「命令行 export 的值优先于 .env 文件」。

    Args:
        path: .env 文件路径，默认取项目根目录的 .env
        override: True 时用 .env 的值强制覆盖已有环境变量

    Returns:
        int: 实际注入的环境变量条数
    """
    if path is None:
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '.env'
        )
    if not os.path.exists(path):
        return 0

    count = 0
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('export '):
                    line = line[7:].strip()
                if '=' not in line:
                    continue
                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip()
                # 剥离成对引号
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                if not key:
                    continue
                if override or key not in os.environ:
                    os.environ[key] = value
                    count += 1
    except (IOError, OSError) as e:
        log.warning(f'.env 加载失败: {e}')
    return count


# 模块导入时自动加载一次
load_dotenv()


# ─── 小米开放平台应用凭证 ───────────────────────────────────────────────
# 通过环境变量或 .env 注入，源码中不保留任何默认值。
# 见 .env.example。

def get_xiaomi_credentials():
    """读取小米开放平台应用凭证。

    Returns:
        tuple[str, str]: (client_id, client_secret)，未配置时为空字符串
    """
    return (
        os.environ.get('XIAOMI_CLIENT_ID', '').strip(),
        os.environ.get('XIAOMI_CLIENT_SECRET', '').strip(),
    )


CLIENT_ID, CLIENT_SECRET = get_xiaomi_credentials()


# ════════════════════════════════════════════════════════════════════════
#  Part 1: 用户信息直读（Token 文件模式）
# ════════════════════════════════════════════════════════════════════════

def load_user_info():
    """从 userinfo.txt 加载用户凭证信息。

    文件格式: JSON 字符串，必须包含 authorization 字段。
    可选字段: userAgent, deviceId 等。

    Returns:
        dict: 解析后的用户信息字典

    Raises:
        FileNotFoundError: 文件不存在时自动创建并提示
        ValueError: 文件为空或 JSON 格式错误
    """
    if not os.path.exists(USERINFO_FILE):
        # 自动创建空文件，引导用户填写
        with open(USERINFO_FILE, 'w', encoding='utf-8') as f:
            f.write('')
        raise FileNotFoundError(
            f'未找到 userinfo.txt，已自动创建: {USERINFO_FILE}\n'
            f'请写入小爱课程表抓包得到的 userinfo JSON 字符串后重试。'
        )

    with open(USERINFO_FILE, 'r', encoding='utf-8') as f:
        raw = f.read().strip()

    if not raw:
        raise ValueError('userinfo.txt 内容为空。')

    try:
        info = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f'userinfo.txt 不是有效 JSON 字符串: {e}')

    if not info.get('authorization'):
        raise ValueError('userinfo.txt 缺少 authorization 字段。')

    return info


# ════════════════════════════════════════════════════════════════════════
#  Part 2: 小米账号认证器（密码登录）
# ════════════════════════════════════════════════════════════════════════

class XiaomiAuthenticator:
    """小米账号 OAuth2 密码认证器。

    完整的 5 步 OAuth2 流程:
      Step 1: 预登录获取 _sign 和 qs
      Step 2: 提交凭据（手机号 + MD5 密码哈希）
      Step 3: 跟随 location 重定向获取 cookies
      Step 4: OAuth2 授权升级
      Step 5: 签发 Access Token

    支持安全验证拦截 → browser_login() 浏览器辅助验证。

    Usage:
        auth = XiaomiAuthenticator('13800138000', 'password', device_uuid)
        token, error = auth.get_new_token()
        if auth.need_verification:
            auth.browser_login(auth.notification_url)
    """

    def __init__(self, phone, password, device_uuid):
        self.user = phone
        self.password_hash = hashlib.md5(password.encode()).hexdigest().upper()
        self.session = requests.Session()
        self.device_id = hashlib.md5(
            (device_uuid if device_uuid else str(uuid.uuid4())).encode()
        ).hexdigest()
        scope_dict = {'d': self.device_id}
        self.scope_data = base64.urlsafe_b64encode(
            json.dumps(scope_dict, separators=(',', ':')).encode()
        ).decode().strip('=')
        self.session.headers.update({
            'User-Agent': (
                'Dalvik/2.1.0 (Linux; Android 16; 22081212C '
                'Build/BP2A.250605.031.A3) APP/xiaomi.aischedule '
                'APPV/101001000 MK/UkVETUkgSzUwIFVsdHJh '
                'PassportSDK/3.8.4.test.bugfix passport-ui/3.8.4.test.bugfix'
            ),
            'X-Passport-Device-Id': self.device_id,
        })
        self._saved = None  # 验证拦截时存储 notificationUrl

    def log_debug(self, msg):
        log.debug(msg)

    def _parse_json(self, text):
        """解析小米 API 返回的 JSON（处理 &&&START&&& 前缀）"""
        try:
            if text.startswith('&&&START&&&'):
                return json.loads(text.replace('&&&START&&&', ''))
            return json.loads(text)
        except (json.JSONDecodeError, ValueError) as e:
            self.log_debug(f'JSON 解析失败: {e}, text[:200]={text[:200]}')
            return {'_parse_error': str(e), '_text': text[:500]}

    @property
    def need_verification(self):
        """是否需要安全验证（返回 True 表示被拦截）"""
        return bool(self._saved)

    @property
    def notification_url(self):
        """安全验证通知 URL"""
        return self._saved or ''

    def get_new_token(self):
        """执行完整的 OAuth2 登录流程获取 Token。

        Returns:
            tuple: (token_string | None, error_message | None)
                   token 为 None 但 error 也为 None 时表示 need_verification
        """
        # ── Step 1: 预登录 ──
        try:
            prep_resp = self.session.get(
                ('https://account.xiaomi.com/pass/serviceLogin'
                 '?_json=true&sid=ai-service&_locale=zh_CN'
                 f'&deviceId={self.device_id}'),
                timeout=15,
            )
            prep = self._parse_json(prep_resp.text)
        except Exception as e:
            log.error(f'Auth Step1 (pre-login) failed: {e}')
            return None, f'预登录请求失败: {e}'

        # ── Step 2: 提交凭据 ──
        try:
            auth_resp = self.session.post(
                'https://account.xiaomi.com/pass/serviceLoginAuth2',
                data={
                    'cc': '+86',
                    'user': self.user,
                    'hash': self.password_hash,
                    'sid': 'ai-service',
                    '_json': 'true',
                    '_sign': prep.get('_sign'),
                    'qs': prep.get('qs'),
                    'callback': 'https://account.ai.xiaomi.com/sts',
                    'deviceId': self.device_id,
                },
                timeout=15,
            )
            auth = self._parse_json(auth_resp.text)
        except Exception as e:
            log.error(f'Auth Step2 (login post) failed: {e}')
            return None, f'登录请求失败: {e}'

        code = auth.get('code')
        if code != 0:
            desc = auth.get('description', auth.get('desc', ''))
            log.warning(
                f'Auth Step2 rejected: code={code}, desc={desc}, '
                f'full={json.dumps(auth, ensure_ascii=False)[:300]}'
            )
            code_messages = {
                87001: '用户名或密码错误',
                70016: '需要安全验证（可能是验证码/短信），请稍后重试或尝试在手机端登录后再使用',
                70002: '账号异常，请先在小米官网解锁',
                70003: '密码错误次数过多，请稍后重试',
            }
            msg = code_messages.get(code, f'小米账号验证失败 (code={code})')
            if desc:
                msg += f' [{desc}]'
            return None, msg

        location = auth.get('location')
        notification_url = auth.get('notificationUrl', '')

        if not location:
            log.warning(f'Auth Step2 no location, notificationUrl={bool(notification_url)}')
            if notification_url:
                self._saved = notification_url
                return None, None  # 调用方检查 need_verification
            log.warning('Auth Step2 no location and no notificationUrl')
            return None, '登录响应异常，无法继续'

        # ── Step 3: 跟随 location 重定向 ──
        try:
            self.session.get(location, timeout=15)
        except Exception as e:
            log.error(f'Auth Step3 (follow location) failed: {e}')
            return None, f'登录跳转失败: {e}'

        # ── Step 4: OAuth2 授权升级 ──
        try:
            oauth_resp = self.session.get(
                ('https://account.xiaomi.com/pass/serviceLogin'
                 '?_json=true&appName=com.xiaomi.aischedule'
                 '&sid=oauth2.0&_locale=zh_CN'
                 f'&deviceId={self.device_id}'),
                timeout=15,
            )
            oauth = self._parse_json(oauth_resp.text)
            log.debug(
                f'Auth Step4 response: code={oauth.get("code")}, '
                f'has_location={bool(oauth.get("location"))}'
            )
        except Exception as e:
            log.error(f'Auth Step4 (oauth) failed: {e}')
            return None, f'OAuth2 授权请求失败: {e}'

        if oauth.get('location'):
            try:
                self.session.get(oauth['location'], timeout=15)
                st = self.session.cookies.get(
                    'serviceToken', domain='.account.xiaomi.com'
                )
                if st:
                    self.session.cookies.set(
                        'oauth2.0_serviceToken', st, domain='.xiaomi.com'
                    )
                    log.info('Auth Step4: oauth2.0_serviceToken cookie set')
            except Exception as e:
                log.error(f'Auth Step4 (oauth redirect) failed: {e}')
                return None, f'OAuth2 跳转失败: {e}'

        # ── Step 5: 签发 Access Token ──
        # 凭证从环境变量 /.env 读取，源码不留默认值（开源脱敏）
        cid, client_secret = get_xiaomi_credentials()
        if not cid or not client_secret:
            return None, (
                '未配置小米开放平台凭证，无法签发 Access Token。\n'
                '请在项目根目录创建 .env 文件并填写：\n'
                '  XIAOMI_CLIENT_ID=...\n'
                '  XIAOMI_CLIENT_SECRET=...\n'
                '（可参考 .env.example 模板）'
            )
        try:
            scopes_resp = self.session.get(
                ('https://account.xiaomi.com/oauth2/user-credentials/scopes'
                 f'?client_id={cid}&sid=oauth2.0'),
                timeout=15,
            )
            code = scopes_resp.json().get('code')
        except Exception as e:
            log.error(f'Auth Step5 (scopes) failed: {e}')
            return None, f'获取授权范围失败: {e}'

        try:
            res = self.session.post(
                'https://account.xiaomi.com/oauth2/user-credentials/issued-token',
                data={
                    'grant_type': 'password',
                    'client_id': cid,
                    'user_id': auth.get('userId'),
                    'client_secret': client_secret,
                    'sid': 'oauth2.0',
                    'code': code,
                },
                timeout=15,
            ).json()
        except Exception as e:
            log.error(f'Auth Step5 (token issue) failed: {e}')
            return None, f'获取 Access Token 失败: {e}'

        if 'access_token' in res:
            token = (
                f'AO-TOKEN-V1 dev_app_id:{cid},'
                f'access_token:{res["access_token"]},'
                f'scope_data:{self.scope_data}'
            )
            log.info('Auth success: token obtained')
            return token, None
        else:
            log.warning(f'Auth Step5 no access_token: {json.dumps(res, ensure_ascii=False)[:300]}')
            err_desc = res.get('description', res.get('desc', ''))
            return None, f'未获取到 Access Token{f" ({err_desc})" if err_desc else ""}'

    def browser_login(self, notification_url, progress_dict=None):
        """启动 Playwright 浏览器完成安全验证。

        将 deviceId 注入浏览器上下文，使小米服务器信任此设备。
        用户在浏览器中完成验证后，提取 cookies 回 session。

        Args:
            notification_url: 安全验证 URL
            progress_dict: 可选进度回调字典 {'status': ..., 'message': ...}

        Returns:
            tuple: (success: bool, error_msg: str | None)
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return False, 'Playwright 未安装'

        def _progress(msg, status=None):
            if progress_dict is not None:
                progress_dict['message'] = msg
                if status:
                    progress_dict['status'] = status

        try:
            _progress('正在启动浏览器...')
            with sync_playwright() as p:
                browser = None
                launch_errors = []
                for launcher in [
                    lambda: p.chromium.launch(headless=False, channel='chrome'),
                    lambda: p.chromium.launch(headless=False, channel='msedge'),
                    lambda: p.firefox.launch(headless=False),
                ]:
                    try:
                        browser = launcher()
                        break
                    except Exception as e:
                        launch_errors.append(str(e))
                        continue

                if browser is None:
                    _progress(f'无法启动浏览器: {"; ".join(launch_errors) if launch_errors else "未知"}', 'error')
                    return False, '无法启动浏览器，请确认已安装 Chrome/Edge/Firefox'

                context = browser.new_context(
                    user_agent=self.session.headers.get('User-Agent', '')
                )

                # 注入 deviceId 使小米识别此设备
                for domain in [
                    '.account.xiaomi.com', '.xiaomi.com',
                    '.account.ai.xiaomi.com', '.ai.xiaomi.com',
                ]:
                    try:
                        context.add_cookies([{
                            'name': 'deviceId',
                            'value': self.device_id,
                            'domain': domain,
                            'path': '/',
                        }])
                    except Exception:
                        pass

                # 注入已有 session cookies
                for cookie in self.session.cookies:
                    try:
                        domain = cookie.domain
                        if not domain.startswith('.'):
                            domain = '.' + domain
                        context.add_cookies([{
                            'name': cookie.name,
                            'value': cookie.value,
                            'domain': domain,
                            'path': cookie.path or '/',
                        }])
                    except Exception:
                        pass

                page = context.new_page()
                _progress('请在弹出的浏览器窗口中完成小米安全验证，完成后窗口会自动关闭...')
                page.goto(notification_url)

                # 记录验证开始时的 cookie 快照，用于检测登录态变化
                def _cookie_sig(cookies):
                    return {
                        (c['name'], c.get('domain', '')): c['value']
                        for c in cookies
                        if c['name'] in ('serviceToken', 'userId')
                    }
                base_sig = _cookie_sig(context.cookies())

                # 等待验证完成，三个退出条件（解决永久卡死）:
                #   1. 自动检测到登录态变化（serviceToken/userId 出现新值）→ 自动关闭
                #   2. 用户手动关闭浏览器窗口 → 立即继续
                #   3. 超时（5 分钟）→ 报错退出
                VERIFICATION_TIMEOUT = 300
                deadline = time.time() + VERIFICATION_TIMEOUT
                auto_verified = False
                while time.time() < deadline:
                    if page.is_closed():
                        break
                    try:
                        if _cookie_sig(context.cookies()) != base_sig:
                            auto_verified = True
                            _progress('检测到验证已完成，正在自动关闭浏览器...')
                            page.close()
                            break
                    except Exception:
                        pass  # 页面跳转瞬间 cookies() 可能抖动，忽略
                    page.wait_for_timeout(1000)

                if not page.is_closed():
                    try:
                        page.close()
                    except Exception:
                        pass
                if time.time() >= deadline and not auto_verified:
                    _progress('验证等待超时（5 分钟），请重试', 'error')
                    browser.close()
                    return False, '安全验证超时，请在 5 分钟内完成验证后重试'

                _progress('浏览器已关闭，正在提取会话...')

                # 从 Playwright 提取 cookies 回 requests session
                pw_cookies = context.cookies()
                for c in pw_cookies:
                    try:
                        self.session.cookies.set(
                            c['name'], c['value'],
                            domain=c.get('domain', ''),
                            path=c.get('path', '/'),
                        )
                    except Exception:
                        pass

                browser.close()
                # 同上: done 状态由 verify worker 连同 token 一起写入，此处不提前置 done
                _progress('验证完成')
                return True, None
        except Exception as e:
            _progress(f'浏览器验证出错: {e}', 'error')
            return False, f'浏览器验证过程出错: {e}'


# ════════════════════════════════════════════════════════════════════════
#  Part 3: 小米扫码登录
# ════════════════════════════════════════════════════════════════════════

def xiaoai_qr_login(progress_dict=None):
    """小米扫码登录：打开浏览器显示二维码，手机扫码后自动获取 Token。

    Args:
        progress_dict: 可选进度回调字典

    Returns:
        tuple: (token_string | None, error_message | None)
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, 'Playwright 未安装'

    def _progress(msg, status=None):
        if progress_dict is not None:
            progress_dict['message'] = msg
            if status:
                progress_dict['status'] = status

    try:
        _progress('正在启动浏览器...')
        with sync_playwright() as p:
            browser = None
            launch_errors = []
            for launcher in [
                lambda: p.chromium.launch(headless=False, channel='chrome'),
                lambda: p.chromium.launch(headless=False, channel='msedge'),
                lambda: p.firefox.launch(headless=False),
            ]:
                try:
                    browser = launcher()
                    break
                except Exception as e:
                    launch_errors.append(str(e))
                    continue

            if browser is None:
                err_msg = f'无法启动浏览器: {"; ".join(launch_errors) if launch_errors else "未知原因"}'
                return None, err_msg

            context = browser.new_context()
            page = context.new_page()
            _progress('页面加载中，请稍候...')

            # 监听回调 URL: 到达 sts 回调即视为扫码成功，并尝试从参数提取 userId
            # （注: 小米新版回调 sts?ticket=...&auth=... 已不带 userId 参数，
            #   因此到达回调本身就是完成信号，userId 改从 cookies/JSON 兜底提取）
            user_id_from_url = [None]
            sts_reached = [False]

            def handle_response(response):
                if 'account.ai.xiaomi.com/sts' in response.url:
                    sts_reached[0] = True
                    from urllib.parse import parse_qs, urlparse
                    params = parse_qs(urlparse(response.url).query)
                    if 'userId' in params:
                        user_id_from_url[0] = params['userId'][0]

            page.on('response', handle_response)

            page.goto(
                'https://account.xiaomi.com/fe/service/login'
                '?_qr=true&_locale=zh_CN&sid=ai-service'
                '&callback=https%3A%2F%2Faccount.ai.xiaomi.com%2Fsts',
            )

            _progress('请用手机扫描浏览器窗口中的二维码，成功后窗口会自动关闭...')

            # 等待扫码完成，三个退出条件（解决永久卡死）:
            #   1. 浏览器跳转到 sts 回调（扫码成功标志）→ 自动关闭
            #   2. 用户手动关闭浏览器窗口 → 立即继续
            #   3. 超时（5 分钟，二维码本身也会过期）→ 报错退出
            QR_TIMEOUT = 300
            deadline = time.time() + QR_TIMEOUT
            auto_done = False
            while time.time() < deadline:
                if page.is_closed():
                    break
                if user_id_from_url[0] or sts_reached[0]:
                    auto_done = True
                    _progress('检测到扫码登录成功，正在自动关闭浏览器...')
                    try:
                        # 略等片刻确保登录 cookie（serviceToken/userId）写入完毕
                        page.wait_for_timeout(1500)
                        page.close()
                    except Exception:
                        pass
                    break
                page.wait_for_timeout(1000)

            if not page.is_closed():
                try:
                    page.close()
                except Exception:
                    pass
            if time.time() >= deadline and not auto_done:
                _progress('扫码等待超时（5 分钟）', 'error')
                browser.close()
                return None, '扫码登录超时，二维码可能已过期，请重试'

            _progress('浏览器已关闭，正在提取会话...')

            pw_cookies = context.cookies()
            # 诊断日志: 记录扫码后实际拿到的 cookie（值不落盘，只记名称和域）
            log.info(
                'QR login cookies: '
                + json.dumps(
                    [
                        {'name': c.get('name'), 'domain': c.get('domain', '')}
                        for c in pw_cookies
                    ],
                    ensure_ascii=False,
                )
            )
            session = requests.Session()
            for c in pw_cookies:
                try:
                    session.cookies.set(
                        c['name'], c['value'],
                        domain=c.get('domain', ''),
                        path=c.get('path', '/'),
                    )
                except Exception:
                    pass

            # 验证是否真的完成了登录。
            # 注: 扫码登录的 serviceToken 种在 .ai.xiaomi.com 域（sts 回调域），
            #     account.xiaomi.com 域则通常有 passToken/userId ——
            #     因此不能只认 account.xiaomi.com 域的 serviceToken。
            has_auth = any(
                c.get('name') in ('serviceToken', 'passToken')
                and 'xiaomi.com' in c.get('domain', '')
                for c in pw_cookies
            )
            if not has_auth:
                return None, '未检测到登录状态，请确认已使用手机扫码完成登录'

            # 生成设备身份用于后续 Token 签发
            device_uuid = str(uuid.uuid4())
            device_id = hashlib.md5(device_uuid.encode()).hexdigest()
            scope_dict = {'d': device_id}
            scope_data = base64.urlsafe_b64encode(
                json.dumps(scope_dict, separators=(',', ':')).encode()
            ).decode().strip('=')

            # 凭证从环境变量 /.env 读取（开源脱敏）
            cid, _client_secret_unused = get_xiaomi_credentials()
            if not cid:
                return None, (
                    '未配置小米开放平台凭证。请在项目根目录创建 .env 文件并填写：\n'
                    '  XIAOMI_CLIENT_ID=...\n'
                    '（可参考 .env.example 模板）'
                )

            def _parse_xiaomi_json(text):
                """解析小米 API 响应（处理 &&&START&&& 前缀）"""
                if text.startswith('&&&START&&&'):
                    text = text.replace('&&&START&&&', '')
                return json.loads(text)

            def _oauth_upgrade(sess, dev_id):
                """请求 sid=oauth2.0 的 serviceLogin 并跟随 location，
                建立 oauth2.0 登录态。返回响应 JSON。"""
                resp = sess.get(
                    ('https://account.xiaomi.com/pass/serviceLogin'
                     '?_json=true&appName=com.xiaomi.aischedule'
                     '&sid=oauth2.0&_locale=zh_CN'
                     f'&deviceId={dev_id}'),
                    timeout=15,
                )
                data = _parse_xiaomi_json(resp.text)
                if data.get('location'):
                    sess.get(data['location'], timeout=15)
                    st = sess.cookies.get(
                        'serviceToken', domain='.account.xiaomi.com'
                    )
                    if st:
                        sess.cookies.set(
                            'oauth2.0_serviceToken', st, domain='.xiaomi.com'
                        )
                return data

            # OAuth2 升级: serviceToken → oauth2.0 sid
            oauth = {}  # 失败时兜底空字典，供后续 userId 提取使用
            try:
                oauth = _oauth_upgrade(session, device_id)

                if not oauth.get('location'):
                    # account.xiaomi.com 域暂无登录态（扫码 token 种在 ai 域）——
                    # 先用 ai-service sid 免密续签建立主站登录态，再重试升级
                    log.info('QR OAuth2 no location, trying ai-service sid renewal')
                    pre_resp = session.get(
                        ('https://account.xiaomi.com/pass/serviceLogin'
                         '?_json=true&sid=ai-service&_locale=zh_CN'
                         f'&deviceId={device_id}'),
                        timeout=15,
                    )
                    pre = _parse_xiaomi_json(pre_resp.text)
                    if pre.get('location'):
                        session.get(pre['location'], timeout=15)
                        oauth = _oauth_upgrade(session, device_id)
                        log.info(
                            'QR OAuth2 retry after renewal: '
                            f"has_location={bool(oauth.get('location'))}"
                        )
            except Exception as e:
                log.warning(f'QR OAuth2 step failed: {e}')

            # userId 提取链（新版回调不带 userId 参数，多路兜底）:
            #   1. sts 回调 URL 参数（旧版格式）
            #   2. Playwright 捕获的 cookies（任意域的 userId cookie）
            #   3. requests session cookies（.account.xiaomi.com 域）
            #   4. serviceLogin JSON 响应中的 userId / cUserId 字段
            uid = user_id_from_url[0]
            if not uid:
                for c in pw_cookies:
                    if c.get('name') == 'userId' and c.get('value'):
                        uid = c['value']
                        break
            if not uid:
                uid = session.cookies.get(
                    'userId', domain='.account.xiaomi.com'
                )
            if not uid:
                uid = oauth.get('userId') or oauth.get('cUserId')

            if not uid:
                return None, '未获取到用户ID，请确认扫码登录已完成'

            # 凭证从环境变量 /.env 读取，源码不留默认值（开源脱敏）
            cid, client_secret = get_xiaomi_credentials()
            if not cid or not client_secret:
                return None, (
                    '未配置小米开放平台凭证。请在项目根目录创建 .env 文件并填写：\n'
                    '  XIAOMI_CLIENT_ID=...\n'
                    '  XIAOMI_CLIENT_SECRET=...\n'
                    '（可参考 .env.example 模板）'
                )

            # 获取签发的 Access Token
            try:
                scopes_resp = session.get(
                    ('https://account.xiaomi.com/oauth2/user-credentials/scopes'
                     f'?client_id={cid}&sid=oauth2.0'),
                    timeout=15,
                )
                try:
                    scopes_data = scopes_resp.json()
                except (ValueError, json.JSONDecodeError):
                    return None, '获取授权范围响应格式异常'
                code = scopes_data.get('code')

                if not code:
                    return None, '获取授权码失败，请确认已成功扫码登录'

                res = session.post(
                    'https://account.xiaomi.com/oauth2/user-credentials/issued-token',
                    data={
                        'grant_type': 'password',
                        'client_id': cid,
                        'user_id': uid,
                        'client_secret': client_secret,
                        'sid': 'oauth2.0',
                        'code': code,
                    },
                    timeout=15,
                )
                try:
                    res = res.json()
                except (ValueError, json.JSONDecodeError):
                    return None, '获取登录令牌响应格式异常'

                if 'access_token' in res:
                    token = (
                        f'AO-TOKEN-V1 dev_app_id:{cid},'
                        f'access_token:{res["access_token"]},'
                        f'scope_data:{scope_data}'
                    )
                    # 注意: 此处不得提前置 status='done' —— done 必须由
                    # 调用方 worker 连同 token 一起写入，否则 finalize
                    # 会在 token 落库前抢跑，取到 None
                    _progress('扫码登录成功')
                    return token, None

                err_desc = res.get('description', res.get('desc', 'unknown error'))
                return None, f'获取登录令牌失败: {err_desc}'

            except Exception as e:
                return None, f'获取登录令牌失败: {e}'

    except Exception as e:
        _progress(f'扫码登录出错: {e}', 'error')
        return None, f'扫码登录过程出错: {e}'


# ════════════════════════════════════════════════════════════════════════
#  Part 4: 教务系统课表解析
# ════════════════════════════════════════════════════════════════════════

class QiangZhiParser:
    """HNUST 教务系统 HTML 课表解析器。

    解析 kdjw.hnust.edu.cn 的 timetable 页面，
    提取课程名称、教师、教室、星期、节次、周次等信息。

    Usage:
        courses = QiangZhiParser.parse_html(html_content)
        for c in courses:
            print(c['name'], c['teacher'], c['position'])
    """

    @staticmethod
    def parse_weeks(raw):
        """从周次字符串解析为排序去重的周次列表字符串。

        Examples:
            "1-10(周)" → "1,2,3,4,5,6,7,8,9,10"
            "1,3,5(周)" → "1,3,5"
            "1-5,10(周)" → "1,2,3,4,5,10"
        """
        m = re.search(r'([\d\-,]+)\(周\)', raw)
        if not m:
            return ''
        ws = []
        for part in m.group(1).split(','):
            if '-' in part:
                s, e = map(int, part.split('-'))
                ws.extend(range(s, e + 1))
            else:
                ws.append(int(part))
        return ','.join(map(str, sorted(list(set(ws)))))

    @staticmethod
    def parse_html(html):
        """解析教务系统课表 HTML，返回课程列表。

        Args:
            html: 包含 #timetable 表格的 HTML 字符串

        Returns:
            list[dict]: 课程字典列表，每项包含 name/teacher/position/day/sections/weeks
        """
        soup = BeautifulSoup(html, 'html.parser')
        table = soup.find('table', id='timetable')
        if not table:
            return []

        # 行索引 → 节次映射 (跳过标题行和非课程行)
        sec_map = {
            1: '1,2', 2: '3,4', 3: '5,6',
            4: '7,8', 5: '9,10',
        }
        courses = []
        for r_idx, row in enumerate(table.find_all('tr')[1:]):
            if (r_idx + 1) not in sec_map:
                continue
            cur_secs = sec_map[r_idx + 1]
            for d_idx, cell in enumerate(row.find_all('td')):
                divs = cell.find_all('div', class_='kbcontent')
                for div in divs:
                    # 用长横线分割同一单元格的多门课程
                    for b_html in re.split(r'-{10,}', str(div)):
                        b = BeautifulSoup(b_html, 'html.parser')
                        name = b.get_text(separator='|').split('|')[0].strip()
                        if not name or name == ' ':
                            continue

                        t = (
                            b.find('font', title='教师').text.strip()
                            if b.find('font', title='教师') else '未知'
                        )
                        p = (
                            b.find('font', title='教室').text.strip()
                            if b.find('font', title='教室') else '未知'
                        )
                        w_font = b.find('font', title=re.compile('周次'))
                        w = QiangZhiParser.parse_weeks(w_font.text) if w_font else ''

                        courses.append({
                            'name': name,
                            'teacher': t,
                            'position': p,
                            'day': d_idx + 1,
                            'sections': cur_secs,
                            'weeks': w,
                        })
        return courses


# ════════════════════════════════════════════════════════════════════════
#  Part 5: 浏览器捕获器
# ════════════════════════════════════════════════════════════════════════

class HNUSTCapturer:
    """HNUST 教务系统课表 Playwright 捕获器。

    启动真实浏览器访问教务系统，监听网络请求中的
    xskb_list.do 响应并保存到本地文件。

    Usage:
        success = HNUSTCapturer.run(debug_mode=True)
    """

    @staticmethod
    def run(debug_mode=False):
        output_file = LOCAL_KB_FILE
        count = 0
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                if debug_mode:
                    print('\n' + '=' * 40)
                    print('   === 湖南科技大学课表获取中 ===')
                    print('=' * 40)
                    print(
                        '操作提示: 在浏览器完成登录并切换到目标课表,\n'
                        '确认切换后请【手动关闭浏览器窗口】完成获取。'
                    )
                    input('请按回车继续......')

                # 按优先级尝试不同浏览器
                browser = None
                launch_errors = []
                for launcher in [
                    lambda: p.chromium.launch(headless=False, channel='chrome'),
                    lambda: p.chromium.launch(headless=False, channel='msedge'),
                    lambda: p.firefox.launch(headless=False),
                ]:
                    try:
                        browser = launcher()
                        break
                    except Exception as e:
                        launch_errors.append(str(e))
                        continue

                if browser is None:
                    if debug_mode:
                        print(f'无法启动任何浏览器: {"; ".join(launch_errors)}')
                    return False

                context = browser.new_context()
                page = context.new_page()

                def handle(resp):
                    nonlocal count
                    if page.is_closed():
                        return
                    if 'xskb_list.do' in resp.url and resp.status == 200:
                        content = resp.text()
                        if 'timetable' in content:
                            with open(output_file, 'w', encoding='utf-8') as f:
                                f.write(content)
                            count += 1
                            if debug_mode:
                                ts = time.strftime('%H:%M:%S')
                                print(f'[捕捉成功] 第 {count} 次 | 时间: {ts}')

                page.on('response', handle)
                page.goto('https://kdjw.hnust.edu.cn/')
                while not page.is_closed():
                    page.wait_for_timeout(500)

                browser.close()
                return count > 0
        except Exception:
            if debug_mode:
                raise
            return False


# ════════════════════════════════════════════════════════════════════════
#  Part 6: 小爱课表云端 API 客户端（★ 双模式认证 ★）
# ════════════════════════════════════════════════════════════════════════

class XiaoAiCourse:
    """小爱课表云端 API 客户端（支持双模式认证）。

    认证模式:
      ├── 模式 A — Token 字符串（旧版兼容）
      │   api = XiaoAiCourse("AO-TOKEN-V1 app_id:<你的APPID>, access_token:<你的TOKEN>")
      │
      └── 模式 B — User Info Dict（新版，支持设备指纹伪装）
          api = XiaoAiCourse({
              "authorization": "DO-TOKEN-V1 app_id:<你的APPID>...",
              "userAgent": "<你的设备 UA，从抓包结果里复制>",
          })

    API 端点: https://i.xiaomixiaoai.com (新域名)
    Source:   course-app-miui (新版标识)

    凭证配置:
      小米开放平台应用凭证通过环境变量注入（见 .env.example），
      不在源码中保留任何默认值。
    """

    # ── 常量定义 ──
    SOURCE_NAME = 'course-app-miui'
    BASE_URL = 'https://i.xiaomixiaoai.com'

    # 全局连接池：复用 TCP/TLS 连接，避免每次请求都重新握手。
    # 同步 20+ 门课程时，这是"卡顿"的主要来源之一（HTTPS 握手 ~200ms/次）。
    _http = None
    _http_lock = threading.Lock()

    @classmethod
    def _session(cls):
        """获取共享的 requests.Session（懒加载，线程安全）。

        使用 HTTPAdapter 放大连接池，配合批量并行操作。
        """
        if cls._http is None:
            with cls._http_lock:
                if cls._http is None:
                    s = requests.Session()
                    adapter = requests.adapters.HTTPAdapter(
                        pool_connections=16,
                        pool_maxsize=16,
                        max_retries=requests.adapters.Retry(
                            total=2,
                            backoff_factor=0.4,
                            status_forcelist=(429, 500, 502, 503, 504),
                            allowed_methods=frozenset(
                                ['GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'OPTIONS']
                            ),
                        ),
                    )
                    s.mount('https://', adapter)
                    s.mount('http://', adapter)
                    cls._http = s
        return cls._http

    # WebView UA 前缀（模拟小米内置 WebView）
    WEBVIEW_UA_PREFIX = (
        'Mozilla/5.0 (Linux; Android 16; 25102RKBEC '
        'Build/BP2A.250605.031.A3; wv) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Version/4.0 Chrome/148.0.7778.178 '
        'Mobile Safari/537.36'
    )

    def __init__(self, auth, debug_mode=True):
        """初始化 API 客户端。

        Args:
            auth: Token 字符串 或 user_info 字典（含 authorization + 可选 userAgent）
            debug_mode: 是否输出调试日志
        """
        self.debug_mode = debug_mode

        # ── 双模式认证检测与 Headers 构建 ──
        if isinstance(auth, dict):
            # ★ 模式 B: User Info Dict（新）
            self._build_headers_from_info(auth)
        elif isinstance(auth, str):
            # ★ 模式 A: Token 字符串（旧/兼容）
            self._build_headers_from_token(auth)
        else:
            raise TypeError(
                f'auth 参数类型错误: 需要 str 或 dict, 实际收到 {type(auth).__name__}'
            )

    def _build_headers_from_token(self, token):
        """从 Token 字符串构建请求头（兼容模式）"""
        self.headers = {
            'authorization': token,
            'content-type': 'application/json',
            'user-agent': self.WEBVIEW_UA_PREFIX,
            'accept': 'application/json',
            'origin': 'https://i.xiaomixiaoai.com',
            'referer': (
                'https://i.xiaomixiaoai.com/h5/precache/ai-schedule/'
            ),
            'x-requested-with': 'com.miui.voiceassist',
        }

    def _build_headers_from_info(self, user_info):
        """从 user_info 字典构建请求头（新模式）"""
        auth_token = user_info.get('authorization', '')
        device_ua = user_info.get('userAgent', '')

        self.headers = {
            'authorization': auth_token,
            'content-type': 'application/json',
            'user-agent': f'{self.WEBVIEW_UA_PREFIX} {device_ua}'.strip(),
            'accept': 'application/json',
            'origin': 'https://i.xiaomixiaoai.com',
            'referer': (
                'https://i.xiaomixiaoai.com/h5/precache/ai-schedule/'
            ),
            'x-requested-with': 'com.miui.voiceassist',
        }

    def log_debug(self, msg):
        """条件调试日志"""
        if self.debug_mode:
            print(f'[DEBUG] {msg}')

    def _request_id(self):
        """生成唯一请求 ID（UUID 上写十六进制）"""
        return uuid.uuid4().hex.upper()

    def _headers(self, request_id=None):
        """获取请求头副本（可选附加 requestId）"""
        headers = self.headers.copy()
        if request_id:
            headers['requestid'] = request_id
        return headers

    def _request_ok(self, res, retries=1, tag=''):
        """★ 冷连接首包重试：小米 API 复用新建的 TLS 连接时首个请求常被拒。

        现象（实测）：连接池新建连接后，同一个 Token 第一次请求返回
        401/403，紧接着换用已建立的连接重试立刻返回 200。
        这是服务端的连接预热行为，不是凭证失效。

        判据：401/403 且响应体不是业务层的"未授权"语义 → 判定为冷连接假失败。
        最多重试 retries 次，每次换新的 requestId。

        Args:
            res: 首次请求的 Response
            retries: 额外重试次数
            tag: 日志标记

        Returns:
            requests.Response: 最终响应（可能是首次的，也可能是重试后的）
        """
        for attempt in range(retries):
            code = res.status_code
            # 只有 401/403 且是"空体/网关层"响应才算冷连接假失败；
            # 业务层真未授权会带 170 字节左右的 JSON 错误体，那种不重试。
            if code not in (401, 403):
                break
            body = (res.text or '').strip()
            # 真未授权：返回体是明确的 JSON 错误结构
            if body.startswith('{') or len(body) > 300:
                self.log_debug(
                    f'[{tag}] HTTP {code} 业务层响应，判定为凭证问题，不重试'
                )
                break
            self.log_debug(
                f'[{tag}] HTTP {code} 空体响应 → 疑似冷连接假失败，'
                f'重试 {attempt + 1}/{retries}（{len(body)} 字节体）'
            )
            res = self._session().get(
                res.url,
                headers=self._headers(self._request_id()),
                params={'requestId': self._request_id(),
                        'sourceName': self.SOURCE_NAME},
                timeout=30,
            )
            self.log_debug(f'[{tag}] 重试后: HTTP {res.status_code}')
        return res

    # ── API 方法 ──

    def get_tables(self, retries=1):
        """获取云端课表列表。

        Args:
            retries: 冷连接假失败时的额外重试次数（默认 1）

        Returns:
            list[dict] | None: 课表列表，失败返回 None
        """
        self.log_debug('获取云端课表列表...')
        request_id = self._request_id()
        res = self._session().get(
            f'{self.BASE_URL}/course-multi-auth/tables',
            headers=self._headers(request_id),
            params={'requestId': request_id, 'sourceName': self.SOURCE_NAME},
            timeout=30,
        )
        self.log_debug(f'列表响应: HTTP {res.status_code} {res.text[:200]}')

        # ★ 冷连接首包 401/403 → 重试一次
        if res.status_code in (401, 403):
            res = self._request_ok(res, retries=retries, tag='get_tables')

        if res.status_code != 200:
            self.log_debug(f'获取课表列表失败: HTTP {res.status_code}')
            return None
        try:
            return res.json().get('data', [])
        except (ValueError, json.JSONDecodeError) as e:
            self.log_debug(f'课表列表 JSON 解析失败: {e}')
            return None

    def get_detail(self, tid):
        """获取单个课表的详情（含设置和课程列表）。

        Args:
            tid: 课表 ID

        Returns:
            dict | None: 课表详情
        """
        self.log_debug(f'获取课表详情 (ID: {tid})...')
        request_id = self._request_id()
        res = self._session().get(
            f'{self.BASE_URL}/course-multi-auth/table',
            headers=self._headers(request_id),
            params={
                'ctId': tid,
                'requestId': request_id,
                'sourceName': self.SOURCE_NAME,
            },
            timeout=30,
        )
        try:
            return res.json().get('data')
        except (ValueError, json.JSONDecodeError) as e:
            self.log_debug(f'课表详情 JSON 解析失败: {e}')
            return None

    def sync_settings(self, config, name=None, **kw):
        """同步课表基础设置参数（作息时间、学期日期等）。

        Args:
            config: 从 get_detail 获取的课表配置对象
            name: 可选，新名称（用于重命名课表；None 表示保持原名）
            **kw: startSemester, presentWeek, totalWeek, m_num, a_num, n_num, sections_data

        Returns:
            bool: 是否同步成功
        """
        self.log_debug('同步课表基础设置参数...')
        setting = config.get('setting', {}).copy()
        if 'sectionTimes' in setting:
            setting['sections'] = setting.pop('sectionTimes')
        if 'sections_data' in kw:
            setting['sections'] = json.dumps(kw['sections_data'])

        try:
            ts = int(time.mktime(time.strptime(kw.get('startSemester'), '%Y-%m-%d')) * 1000)
        except (ValueError, TypeError) as e:
            self.log_debug(f'学期日期解析失败 (startSemester={kw.get("startSemester")}): {e}，使用当前日期')
            ts = int(time.time() * 1000)
        setting.update({
            'startSemester': str(ts),
            'presentWeek': kw.get('presentWeek'),
            'totalWeek': kw.get('totalWeek'),
            'morningNum': kw.get('m_num'),
            'afternoonNum': kw.get('a_num'),
            'nightNum': kw.get('n_num'),
        })

        request_id = self._request_id()
        res = self._session().put(
            f'{self.BASE_URL}/course-multi-auth/table',
            headers=self._headers(request_id),
            json={
                'ctId': config['id'],
                'name': name if name else config['name'],
                'setting': setting,
                'sourceName': self.SOURCE_NAME,
            },
            timeout=30,
        )
        self.log_debug(f'设置同步结果: {res.text}')
        try:
            return res.json().get('code') == 0
        except (ValueError, json.JSONDecodeError):
            self.log_debug(f'设置同步响应 JSON 解析失败: {res.text[:200]}')
            return False

    def add_c(self, tid, c, style):
        """添加单门课程到指定课表。

        Args:
            tid: 目标课表 ID
            c: 课程字典 {name, teacher, position, day, sections, weeks}
            style: 样式字典 {background, color}

        Returns:
            bool: 是否添加成功
        """
        self.log_debug(
            f"上传: {c.get('name', '?')} | 教师: {c.get('teacher', '?')} "
            f"| 地点: {c.get('position', '?')} ..."
        )
        request_id = self._request_id()
        res = self._session().post(
            f'{self.BASE_URL}/course-multi-auth/courseInfo',
            headers=self._headers(request_id),
            json={
                'ctId': tid,
                'sourceName': self.SOURCE_NAME,
                'course': {
                    'name': c.get('name', ''),
                    'teacher': c.get('teacher', ''),
                    'position': c.get('position', ''),
                    'extend': '',
                    'day': c.get('day', 1),
                    'sections': str(c.get('sections', '')),
                    'weeks': c.get('weeks', ''),
                    'style': json.dumps(style),
                },
            },
            timeout=30,
        )
        result = False
        try:
            result = res.json().get('code') == 0
        except (ValueError, json.JSONDecodeError):
            self.log_debug(f'添加课程响应 JSON 解析失败: {res.text[:200]}')
        if result:
            self.log_debug('成功')
        else:
            try:
                desc = res.json().get('desc', '')
                self.log_debug(f'失败: code={res.json().get("code")}, desc={desc}, resp={res.text[:200]}')
            except (ValueError, json.JSONDecodeError):
                self.log_debug(f'失败: HTTP {res.status_code}, resp={res.text[:200]}')
        return result

    def del_c(self, tid, cid):
        """删除指定课程。

        Args:
            tid: 课表 ID
            cid: 课程 ID

        Returns:
            bool: 是否删除成功
        """
        request_id = self._request_id()
        res = self._session().delete(
            f'{self.BASE_URL}/course-multi-auth/courseInfo',
            headers=self._headers(request_id),
            json={
                'ctId': tid,
                'cId': cid,
                'sourceName': self.SOURCE_NAME,
            },
            timeout=30,
        )
        if not res.ok:
            self.log_debug(f'删除课程失败: HTTP {res.status_code}')
            return False
        try:
            return res.json().get('code') == 0
        except (ValueError, json.JSONDecodeError):
            return res.ok

    # ── 批量并行操作（用于大规模同步，替代逐门串行） ──

    def batch_delete_courses(self, tid, cids, max_workers=6, on_progress=None):
        """并行批量删除课程。

        Args:
            tid: 课表 ID
            cids: 课程 ID 列表
            max_workers: 并发线程数（默认 6，受服务端限流约束）
            on_progress: 可选回调 callable(done, total)

        Returns:
            tuple[list, list]: (成功列表, 失败列表)
        """
        total = len(cids)
        if total == 0:
            return [], []
        done = [0]
        lock = threading.Lock()
        results = {}

        def worker(cid):
            try:
                results[cid] = bool(self.del_c(tid, cid))
            except Exception as e:
                self.log_debug(f'批量删除异常 cid={cid}: {e}')
                results[cid] = False
            finally:
                with lock:
                    done[0] += 1
                    if on_progress:
                        try:
                            on_progress(done[0], total)
                        except Exception:
                            pass

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            list(pool.map(worker, cids))

        ok = [c for c in cids if results.get(c)]
        fail = [c for c in cids if not results.get(c)]
        self.log_debug(f'批量删除完成: 成功 {len(ok)} / 失败 {len(fail)} / 共 {total}')
        return ok, fail

    def batch_add_courses(self, tid, courses, max_workers=6, on_progress=None):
        """并行批量添加课程。

        Args:
            tid: 课表 ID
            courses: [(course_dict, style_dict), ...] 列表
            max_workers: 并发线程数
            on_progress: 可选回调 callable(done, total)

        Returns:
            tuple[list, list]: (成功课程列表, 失败课程列表)
        """
        total = len(courses)
        if total == 0:
            return [], []
        done = [0]
        lock = threading.Lock()
        results = [False] * total

        def worker(idx):
            course, style = courses[idx]
            try:
                results[idx] = bool(self.add_c(tid, course, style))
            except Exception as e:
                self.log_debug(f'批量添加异常 idx={idx}: {e}')
                results[idx] = False
            finally:
                with lock:
                    done[0] += 1
                    if on_progress:
                        try:
                            on_progress(done[0], total)
                        except Exception:
                            pass

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            list(pool.map(worker, range(total)))

        ok = [courses[i][0] for i in range(total) if results[i]]
        fail = [courses[i][0] for i in range(total) if not results[i]]
        self.log_debug(f'批量添加完成: 成功 {len(ok)} / 失败 {len(fail)} / 共 {total}')
        return ok, fail

    def create_t(self, name):
        """创建新的空白课表。

        Args:
            name: 课表名称

        Raises:
            RuntimeError: API 返回失败时抛出
        """
        request_id = self._request_id()
        res = self._session().post(
            f'{self.BASE_URL}/course-multi-auth/table',
            headers=self._headers(request_id),
            json={
                'name': name,
                'current': 0,
                'sourceName': self.SOURCE_NAME,
            },
            timeout=30,
        )
        if not res.ok:
            try:
                body = res.json()
                msg = body.get('message') or body.get('desc') or f'HTTP {res.status_code}'
            except (ValueError, json.JSONDecodeError):
                msg = f'HTTP {res.status_code}'
            raise RuntimeError(f'创建课表失败: {msg}')

    def del_t(self, tid, sid):
        """删除指定课表。

        Args:
            tid: 课表 ID
            sid: 设置 ID

        Raises:
            RuntimeError: API 返回失败时抛出
        """
        request_id = self._request_id()
        res = self._session().delete(
            f'{self.BASE_URL}/course-multi-auth/table',
            headers=self._headers(request_id),
            json={
                'ctId': tid,
                'sId': sid,
                'sourceName': self.SOURCE_NAME,
            },
        )
        self.log_debug(f'删除课表响应: {res.status_code} {res.text[:200]}')
        # 检查 HTTP 状态码
        if not res.ok:
            err_msg = f'删除课表 API 返回 {res.status_code}'
            try:
                body = res.json()
                if isinstance(body, dict):
                    err_msg = body.get('message') or body.get('msg') or err_msg
            except Exception:
                pass
            self.log_debug(err_msg)
            raise RuntimeError(err_msg)
        # 检查业务状态码 (即使 HTTP 200, code 也可能非零)
        try:
            body = res.json()
            if isinstance(body, dict) and body.get('code') is not None and body['code'] != 0:
                err_msg = body.get('message') or body.get('msg') or f'业务错误码: {body["code"]}'
                self.log_debug(err_msg)
                raise RuntimeError(err_msg)
        except RuntimeError:
            raise
        except Exception:
            pass  # 无 JSON 体或字段不完整, 视为成功


# ════════════════════════════════════════════════════════════════════════
#  Part 7: 作息时间表预设
# ════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════
#  作息规则引擎（按学校规则生成，而非写死时间表）
# ════════════════════════════════════════════════════════════════════════
#
#  HNUST 作息规则（来自用户实测确认）：
#    · 单节 45 分钟，共 12 节
#    · 2 节连上：1-2 / 3-4 / 5-6 / 7-8 / 9-10 / 11-12
#    · 组内小课间 10 分钟
#    · 组间大课间 20 分钟（换教室时间）
#    · 上午 1-4 节从 08:00 开始
#    · 第 5 节：冬季 14:30 / 夏季 14:00
#    · 第 9 节（晚修）：冬季 19:30 / 夏季 19:00
#
#  验算：8:00 起 4 节 = 8:00-8:45 → 10 → 8:55-9:40 → 20 → 10:00-10:45 → 10 → 10:55-11:40
#        单组 = 45 + 10 + 45 = 100 分钟，加 20 大课间 = 120 分钟 ✅ 与用户描述一致

# 单节时长（分钟）
SLOT_DURATION = 45
# 组内小课间（分钟）
INNER_BREAK = 10
# 组间大课间（分钟，换教室）
LONG_BREAK = 20
# 每组连上几节
GROUP_SIZE = 2
# 默认总节数
DEFAULT_TOTAL_SECTIONS = 12

# ── 分段边界（同时也是推荐算法的分段依据）──
# 第 5 节起进入下午，第 9 节起进入晚修 —— 与 SCHEDULE_RULES 的锚点一致。
_AFTERNOON_FIRST_SECTION = 5
_EVENING_FIRST_SECTION = 9

# 时间兜底边界（仅当无法按节次判定时使用）
_AFTERNOON_CUTOFF = (12, 0)
_EVENING_CUTOFF = (17, 30)


def _to_minutes(hhmm):
    """'08:30' → 510（分钟）"""
    h, m = str(hhmm).split(':')
    return int(h) * 60 + int(m)


def _to_hhmm(mins):
    """510 → '08:30'（按 24 小时取模）"""
    mins = int(mins) % (24 * 60)
    return f'{mins // 60:02d}:{mins % 60:02d}'


# 作息规则预设：只需定义"每段的起始时间"，其余由规则推导
SCHEDULE_RULES = {
    'winter': {
        'label': '冬季作息',
        'dayStart': '08:00',        # 第 1 节
        'afternoonStart': '14:30',  # 第 5 节
        'eveningStart': '19:30',    # 第 9 节（晚修）
    },
    'summer': {
        'label': '夏季作息',
        'dayStart': '08:00',
        'afternoonStart': '14:00',  # 夏季下午提前
        'eveningStart': '19:00',    # 夏季晚修提前
    },
}


def _build_rule_sections(rule, total=None):
    """按作息规则生成完整节次时间表。

    算法：逐节推进游标。每节 45 分钟；组内间隔 10 分钟，
    跨组间隔 20 分钟（大课间）。遇到"段起点节次"时，
    把游标**直接重置**到该段的锚点时间（这才是关键——
    上午/下午/晚修之间不靠累加，而由校方规定的时间锚定）。

    Args:
        rule: SCHEDULE_RULES 里的单条规则 dict
        total: 总节数，默认 DEFAULT_TOTAL_SECTIONS

    Returns:
        list[dict]: [{'i':1,'s':'08:00','e':'08:45'}, ...]
    """
    total = total or DEFAULT_TOTAL_SECTIONS

    # 段起点节次：按"组"编号推导，与 GROUP_SIZE 保持对齐
    #   group=2 → 上午 1-4 节，下午从第 5 节（第 3 组）起，晚修从第 9 节（第 5 组）起
    afternoon_first = _AFTERNOON_FIRST_SECTION
    evening_first = _EVENING_FIRST_SECTION

    # 段起点：(节次序号, 锚点时间)
    anchors = {
        1: rule.get('dayStart', '08:00'),
        afternoon_first: rule.get('afternoonStart', '14:30'),
        evening_first: rule.get('eveningStart', '19:30'),
    }

    sections = []
    cursor = None

    for idx in range(1, total + 1):
        if idx in anchors:
            # 段起点：直接锚定，不继承上一段的累加结果
            cursor = _to_minutes(anchors[idx])
        elif cursor is None:
            cursor = _to_minutes(anchors[1])

        start = cursor
        end = start + SLOT_DURATION
        sections.append({'i': idx, 's': _to_hhmm(start), 'e': _to_hhmm(end)})

        # 推进游标：判断这一节是不是"组"的最后一节
        is_group_end = (idx % GROUP_SIZE == 0)
        if idx + 1 in anchors:
            continue  # 下一节是段起点，交给下一轮锚定，此处不累加
        cursor = end + (LONG_BREAK if is_group_end else INNER_BREAK)

    return sections


# 兼容旧结构：惰性生成（首次访问时按规则算出）
SCHEDULE_PRESETS = {
    key: {
        'label': rule['label'],
        'rule': dict(rule),
        # 'sections' 由下方 _refresh_presets() 填充
    }
    for key, rule in SCHEDULE_RULES.items()
}


def _refresh_presets():
    """按当前规则重算所有预设的 sections（规则改动后调用）。"""
    for key, rule in SCHEDULE_RULES.items():
        SCHEDULE_PRESETS[key]['rule'] = dict(rule)
        SCHEDULE_PRESETS[key]['label'] = rule.get('label', key)
        SCHEDULE_PRESETS[key]['sections'] = [
            (s['s'], s['e']) for s in _build_rule_sections(rule)
        ]
    return SCHEDULE_PRESETS


_refresh_presets()


def apply_rule_overrides(overrides=None):
    """用前端传入的参数临时覆盖作息规则（不落盘，进程内生效）。

    支持覆盖的字段：
        slotDuration    单节时长（分钟）
        innerBreak      组内小课间（分钟）
        longBreak       组间大课间（分钟）
        groupSize       每组连上几节
        totalSections   总节数
        afternoonStart  第 5 节起始（冬季/夏季可分别传）
        eveningStart    第 9 节起始

    Args:
        overrides: dict，值为 None 的键会被忽略

    Returns:
        dict: 生效后的规则副本 {season: rule}
    """
    o = overrides or {}

    def pick(key, cast=int):
        v = o.get(key)
        if v is None or v == '':
            return None
        try:
            return cast(v)
        except (TypeError, ValueError):
            return None

    slot = pick('slotDuration')
    inner = pick('innerBreak')
    long_b = pick('longBreak')
    group = pick('groupSize')
    afternoon = o.get('afternoonStart')
    evening = o.get('eveningStart')

    # 数值合法性夹紧，避免前端传 0 或超大值把时间表算歪
    if slot is not None:
        slot = max(15, min(120, slot))
    if inner is not None:
        inner = max(0, min(60, inner))
    if long_b is not None:
        long_b = max(0, min(120, long_b))
    if group is not None:
        group = max(1, min(6, group))

    if slot is not None:
        globals()['SLOT_DURATION'] = slot
    if inner is not None:
        globals()['INNER_BREAK'] = inner
    if long_b is not None:
        globals()['LONG_BREAK'] = long_b
    if group is not None:
        globals()['GROUP_SIZE'] = group

    if afternoon and _TIME_RE.match(str(afternoon)):
        SCHEDULE_RULES['winter']['afternoonStart'] = str(afternoon)
        SCHEDULE_RULES['summer']['afternoonStart'] = str(afternoon)
    if evening and _TIME_RE.match(str(evening)):
        SCHEDULE_RULES['winter']['eveningStart'] = str(evening)
        SCHEDULE_RULES['summer']['eveningStart'] = str(evening)

    # 季节性锚点也可以按季节单独覆盖
    for key in ('winter', 'summer'):
        a = o.get(f'{key}AfternoonStart')
        e = o.get(f'{key}EveningStart')
        if a and _TIME_RE.match(str(a)):
            SCHEDULE_RULES[key]['afternoonStart'] = str(a)
        if e and _TIME_RE.match(str(e)):
            SCHEDULE_RULES[key]['eveningStart'] = str(e)

    _refresh_presets()
    return {k: dict(v) for k, v in SCHEDULE_RULES.items()}


# 作息时间表允许的节次数量上下限
MIN_SECTIONS = 2
MAX_SECTIONS = 16

# 节次时间格式校验（HH:MM，24 小时制）
_TIME_RE = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$')

# ── 智能作息推荐：分段边界 ──
# 高校作息天然分成三段，段与段之间有午休/晚饭的长间隔。
# 用真实课程节次反推时，必须按段分别锚定，否则会把"午休"误当成"课间"。
# 分段常量已在文件上方的规则引擎处定义（_AFTERNOON_FIRST_SECTION 等）。


def _parse_section_numbers(raw):
    """把课表里的 sections 字段解析为节次序号集合。

    QiangZhi 输出的形态：'1,2' / '3,4' / '1-2' / '1,2,3,4' / '' 等。

    Args:
        raw: sections 原始值

    Returns:
        list[int]: 升序去重的节次序号
    """
    if raw is None:
        return []
    text = str(raw).strip()
    if not text:
        return []

    nums = set()
    # 统一分隔符，再逐个 token 处理（支持 '1-2' 与 '1,2' 混排）
    for token in re.split(r'[,，、;；\s]+', text):
        if not token:
            continue
        m = re.match(r'^(\d+)\s*[-~—]\s*(\d+)$', token)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            nums.update(range(a, b + 1))
            continue
        if token.isdigit():
            nums.add(int(token))
    return sorted(n for n in nums if n > 0)


def infer_sections_from_courses(courses, season='winter'):
    """★ 智能作息推荐：用真实课表的节次占用反推作息时间表。

    思路（与"拍脑袋预设"的区别）：
      1. 解析每门课的 sections，得到全课表实际用到的小节序号集合
      2. 按学校规则（45 分钟/节、2 节一组、10 小课间、20 大课间、
         第 5 节与第 9 节由季节锚定）生成候选作息表
      3. 覆盖到最大占用节次即可，不无脑补满 —— 推荐的意义是去掉空节

    Args:
        courses: 课程列表 [{'name','sections','day','weeks',...}, ...]
        season: 'winter' | 'summer'

    Returns:
        dict: {
            'sections': [{'i','s','e'}, ...],
            'usedSections': [int, ...],      # 真实被占用的节次序号
            'maxUsed': int,                  # 最大占用节次
            'groups': {'morning': n, 'afternoon': n, 'evening': n},
            'periods': [{'key','label','start','end','count'}],
            'season': str,
            'reason': str,                   # 推荐依据（中文，可直接展示）
        } | None（无有效课时返回 None）
    """
    if not courses:
        return None

    used = set()
    for c in courses:
        used.update(_parse_section_numbers(c.get('sections')))

    if not used:
        return None

    max_used = max(used)
    if max_used < MIN_SECTIONS:
        return None

    rule = SCHEDULE_RULES.get(season) or SCHEDULE_RULES['winter']
    preset_total = DEFAULT_TOTAL_SECTIONS

    # ── 总节数：覆盖到实际最大占用即可，但不超过学校上限 ──
    total = max(max_used, MIN_SECTIONS)
    if total % GROUP_SIZE == 1:
        # 例如用到第 9 节 → 补到 10（成组），避免出现"孤节"
        total += 1
    total = min(total, preset_total)
    total = max(total, MIN_SECTIONS)

    sections = _build_rule_sections(rule, total=total)

    # ── 统计各段节数 ──
    counts = {'morning': 0, 'afternoon': 0, 'evening': 0}
    for s in sections:
        i = s['i']
        if i >= _EVENING_FIRST_SECTION:
            counts['evening'] += 1
        elif i >= _AFTERNOON_FIRST_SECTION:
            counts['afternoon'] += 1
        else:
            counts['morning'] += 1

    # ── 每段起止（用于前端展示）──
    def seg_range(start_i, end_i):
        seg = [s for s in sections if start_i <= s['i'] <= end_i]
        if not seg:
            return None, None, 0
        return seg[0]['s'], seg[-1]['e'], len(seg)

    periods = []
    for key, label, a, b in (
        ('morning', '上午', 1, _AFTERNOON_FIRST_SECTION - 1),
        ('afternoon', '下午', _AFTERNOON_FIRST_SECTION, _EVENING_FIRST_SECTION - 1),
        ('evening', '晚修', _EVENING_FIRST_SECTION, total),
    ):
        st, en, n = seg_range(a, b)
        periods.append({'key': key, 'label': label, 'start': st, 'end': en, 'count': n})

    rule_desc = (
        f'{SLOT_DURATION} 分钟/节，{GROUP_SIZE} 节连上，'
        f'小课间 {INNER_BREAK} 分钟，大课间 {LONG_BREAK} 分钟'
    )
    reason = (
        f'依据课表实际占用：共 {len(used)} 个节次被课程使用，最大到第 {max_used} 节；'
        f'推荐 {total} 节。按本校规则铺排（{rule_desc}），'
        f'第 5 节 {rule["afternoonStart"]}、第 9 节晚修 {rule["eveningStart"]} 起。'
    )

    return {
        'sections': sections,
        'usedSections': sorted(used),
        'maxUsed': max_used,
        'groups': counts,
        'periods': periods,
        'season': season,
        'groupSize': GROUP_SIZE,
        'rule': dict(rule),
        'reason': reason,
    }


def _normalize_sections(raw_sections):
    """把前端传入的节次列表规范化为 [{'i','s','e'}, ...]。

    宽容处理以下几种输入形态：
      - [{'i':1,'s':'08:00','e':'08:45'}, ...]
      - [{'start':'08:00','end':'08:45'}, ...]
      - [['08:00','08:45'], ...]

    非法时间会被丢弃；若最终有效条目不足 MIN_SECTIONS，返回 None
    由调用方决定回退到预设。

    Args:
        raw_sections: 任意形态的节次输入

    Returns:
        list[dict] | None
    """
    if not isinstance(raw_sections, list):
        return None

    out = []
    for item in raw_sections:
        start = end = None
        if isinstance(item, dict):
            start = item.get('s') or item.get('start')
            end = item.get('e') or item.get('end')
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            start, end = item[0], item[1]

        start = str(start).strip() if start is not None else ''
        end = str(end).strip() if end is not None else ''
        if not _TIME_RE.match(start) or not _TIME_RE.match(end):
            continue
        if start >= end:
            # 开始时间必须早于结束时间，否则视为非法
            continue
        out.append({'i': len(out) + 1, 's': start, 'e': end})
        if len(out) >= MAX_SECTIONS:
            break

    if len(out) < MIN_SECTIONS:
        return None
    return out


def generate_time_table(season='winter', custom_sections=None):
    """生成节次时间表（支持自定义微调）。

    Args:
        season: 'winter' | 'summer'，用于预设回退
        custom_sections: 可选的用户自定义节次（前端微调结果）。
            传入且合法时优先生效；否则回退到 season 预设。

    Returns:
        list[dict]: [{'i': 1, 's': '08:00', 'e': '08:45'}, ...]
    """
    if custom_sections is not None:
        normalized = _normalize_sections(custom_sections)
        if normalized:
            return normalized
        # 自定义非法 → 静默回退预设（不中断同步）

    preset = SCHEDULE_PRESETS.get(season) or SCHEDULE_PRESETS['winter']
    return [
        {'i': i + 1, 's': start, 'e': end}
        for i, (start, end) in enumerate(preset['sections'])
    ]


# ════════════════════════════════════════════════════════════════════════
#  Part 8: 学期名称推算（26-27-1 / 26-27-2 格式）
# ════════════════════════════════════════════════════════════════════════

def semester_name(year, term):
    """按「学年-学期」格式生成课表名称。

    中国高校学年跨自然年：9 月开学进入新学年上期，次年 2-3 月为下期。

    Args:
        year: 学年起始年份（如 2026 表示 2026-2027 学年）
        term: 1（上期/秋季）| 2（下期/春季）

    Returns:
        str: 形如 '26-27-1' / '26-27-2'
    """
    y = int(year)
    t = 2 if str(term).strip() in ('2', '下', '下期', '春季') else 1
    return f'{y % 100:02d}-{(y + 1) % 100:02d}-{t}'


def current_semester_year(now=None):
    """推算当前所属学年的起始年份。

    9 月及以后 → 本年为学年起始年（如 2026-09 → 2026）
    1-8 月    → 上一年为学年起始年（如 2026-03 → 2025）

    Args:
        now: 可选的 datetime，便于测试；默认取当前时间

    Returns:
        int: 学年起始年份
    """
    now = now or datetime.now()
    return now.year if now.month >= 9 else now.year - 1


def suggest_semester_terms(now=None, span=2):
    """生成「最近若干个学期」的预设名称列表（按时间倒序）。

    Args:
        now: 可选的 datetime
        span: 前后各覆盖多少学年

    Returns:
        list[dict]: [{'name': '26-27-1', 'label': '2026-2027 学年 · 上学期', 'year': 2026, 'term': 1}, ...]
    """
    now = now or datetime.now()
    base_year = current_semester_year(now)
    term = 1 if now.month >= 9 else 2

    # 从当前学期开始，向前回溯 total 个学期
    out = []
    y, t = base_year, term
    total = span * 2 + 1
    for _ in range(total):
        out.append({
            'name': semester_name(y, t),
            'label': f'{y}-{y + 1} 学年 · {"上" if t == 1 else "下"}学期',
            'year': y,
            'term': t,
        })
        # 向前推一个学期
        if t == 1:
            y, t = y - 1, 2
        else:
            y, t = y, 1

    # 再补几个未来的学期（下一学年及之后的）
    y, t = base_year, term
    future = []
    for _ in range(span * 2):
        if t == 2:
            y, t = y + 1, 1
        else:
            y, t = y, 2
        future.append({
            'name': semester_name(y, t),
            'label': f'{y}-{y + 1} 学年 · {"上" if t == 1 else "下"}学期',
            'year': y,
            'term': t,
        })

    merged = future[::-1] + out
    # 去重（保持顺序）
    seen = set()
    uniq = []
    for item in merged:
        if item['name'] in seen:
            continue
        seen.add(item['name'])
        uniq.append(item)
    return uniq
