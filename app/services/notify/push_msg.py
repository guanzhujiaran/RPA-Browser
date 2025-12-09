import base64
import hashlib
import hmac
import json
import re
import threading
import time
import urllib.parse
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
import asyncio
import inspect
from typing import Type
from app.models.RPA_browser.notify_model import NotificationConfig
from app.utils.decorator import log_class_decorator
from app.utils.http import httpx_client
from app.utils.http.rand_headers_gen import rand_fingerprint_generator
import loguru


@log_class_decorator.decorator
class PushMessageService:
    """
    统一推送消息服务类，整合所有推送渠道到一个类中
    """
    logger: Type[loguru.logger]

    def __init__(self, conf: NotificationConfig):
        self.conf = conf

    async def bark(self, title: str, content: str) -> None:
        """
        使用 bark 推送消息。
        """
        if not self.conf.bark_push:
            return
        self.logger.info("bark 服务启动")

        if self.conf.bark_push.startswith("http"):
            url = f'{self.conf.bark_push}'
        else:
            url = f'https://api.day.app/{self.conf.bark_push}'

        bark_params = {
            "BARK_ARCHIVE": "isArchive",
            "BARK_GROUP": "group",
            "BARK_SOUND": "sound",
            "BARK_ICON": "icon",
            "BARK_LEVEL": "level",
            "BARK_URL": "url",
        }
        data = {
            "title": title,
            "body": content,
        }

        # 构建配置字典用于过滤
        config_dict = {
            "BARK_ARCHIVE": self.conf.bark_archive,
            "BARK_GROUP": self.conf.bark_group,
            "BARK_SOUND": self.conf.bark_sound,
            "BARK_ICON": self.conf.bark_icon,
            "BARK_LEVEL": self.conf.bark_level,
            "BARK_URL": self.conf.bark_url,
        }

        for pair in filter(
                lambda pairs: pairs[0].startswith("BARK_")
                              and pairs[0] != "BARK_PUSH"
                              and pairs[1]
                              and bark_params.get(pairs[0]),
                config_dict.items(),
        ):
            data[bark_params.get(pair[0])] = pair[1]
        headers = {"Content-Type": "application/json;charset=utf-8"}

        response = await httpx_client.post(
            url=url, data=json.dumps(data), headers=headers, timeout=15
        )
        response_data = response.json()

        if response_data["code"] == 200:
            self.logger.info("bark 推送成功！")
        else:
            self.logger.error("bark 推送失败！")

    async def dingding_bot(self, title: str, content: str) -> None:
        """
        使用 钉钉机器人 推送消息。
        """
        if not self.conf.dd_bot_secret or not self.conf.dd_bot_token:
            return
        self.logger.info("钉钉机器人 服务启动")

        timestamp = str(round(time.time() * 1000))
        secret_enc = self.conf.dd_bot_secret.encode("utf-8")
        string_to_sign = "{}\n{}".format(timestamp, self.conf.dd_bot_secret)
        string_to_sign_enc = string_to_sign.encode("utf-8")
        hmac_code = hmac.new(
            secret_enc, string_to_sign_enc, digestmod=hashlib.sha256
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        url = f'https://oapi.dingtalk.com/robot/send?access_token={self.conf.dd_bot_token}&timestamp={timestamp}&sign={sign}'
        headers = {"Content-Type": "application/json;charset=utf-8"}
        data = {"msgtype": "text", "text": {"content": f"{title}\n\n{content}"}}

        response = await httpx_client.post(
            url=url, data=json.dumps(data), headers=headers, timeout=15
        )
        response_data = response.json()

        if not response_data["errcode"]:
            self.logger.info("钉钉机器人 推送成功！")
        else:
            self.logger.error("钉钉机器人 推送失败！")

    async def feishu_bot(self, title: str, content: str) -> None:
        """
        使用 飞书机器人 推送消息。
        """
        if not self.conf.fskey:
            return
        self.logger.info("飞书 服务启动")

        url = f'https://open.feishu.cn/open-apis/bot/v2/hook/{self.conf.fskey}'
        data = {"msg_type": "text", "content": {"text": f"{title}\n\n{content}"}}

        response = await httpx_client.post(url, data=json.dumps(data))
        response_data = response.json()

        if response_data.get("StatusCode") == 0 or response_data.get("code") == 0:
            self.logger.info("飞书 推送成功！")
        else:
            self.logger.error(f"飞书 推送失败！错误信息如下：\n{response_data}")

    async def go_cqhttp(self, title: str, content: str) -> None:
        """
        使用 go_cqhttp 推送消息。
        """
        if not self.conf.gobot_url or not self.conf.gobot_qq:
            return
        self.logger.info("go-cqhttp 服务启动")

        url = f'{self.conf.gobot_url}?access_token={self.conf.gobot_token}&{self.conf.gobot_qq}&message=标题:{title}\n内容:{content}'

        response = await httpx_client.get(url)
        response_data = response.json()

        if response_data["status"] == "ok":
            self.logger.info("go-cqhttp 推送成功！")
        else:
            self.logger.error("go-cqhttp 推送失败！")

    async def gotify(self, title: str, content: str) -> None:
        """
        使用 gotify 推送消息。
        """
        if not self.conf.gotify_url or not self.conf.gotify_token:
            return
        self.logger.info("gotify 服务启动")

        url = f'{self.conf.gotify_url}/message?token={self.conf.gotify_token}'
        data = {
            "title": title,
            "message": content,
            "priority": self.conf.gotify_priority,
        }

        response = await httpx_client.post(url, data=data)
        response_data = response.json()

        if response_data.get("id"):
            self.logger.info("gotify 推送成功！")
        else:
            self.logger.error("gotify 推送失败！")

    async def iGot(self, title: str, content: str) -> None:
        """
        使用 iGot 推送消息。
        """
        if not self.conf.igot_push_key:
            return
        self.logger.info("iGot 服务启动")

        url = f'https://push.hellyw.com/{self.conf.igot_push_key}'
        data = {"title": title, "content": content}
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        response = await httpx_client.post(url, data=data, headers=headers)
        response_data = response.json()

        if response_data["ret"] == 0:
            self.logger.info("iGot 推送成功！")
        else:
            self.logger.error(f'iGot 推送失败！{response_data["errMsg"]}')

    async def serverJ(self, title: str, content: str) -> None:
        """
        通过 serverJ 推送消息。
        """
        if not self.conf.push_key:
            return
        self.logger.info("serverJ 服务启动")

        data = {"text": title, "desp": content.replace("\n", "\n\n")}

        match = re.match(r"sctp(\d+)t", self.conf.push_key)
        if match:
            num = match.group(1)
            url = f'https://{num}.push.ft07.com/send/{self.conf.push_key}.send'
        else:
            url = f'https://sctapi.ftqq.com/{self.conf.push_key}.send'

        response = await httpx_client.post(url, data=data)
        response_data = response.json()

        if response_data.get("errno") == 0 or response_data.get("code") == 0:
            self.logger.info("serverJ 推送成功！")
        else:
            self.logger.error(f'serverJ 推送失败！错误码：{response_data["message"]}')

    async def pushdeer(self, title: str, content: str) -> None:
        """
        通过PushDeer 推送消息
        """
        if not self.conf.deer_key:
            return
        self.logger.info("PushDeer 服务启动")
        data = {
            "text": title,
            "desp": content,
            "type": "markdown",
            "pushkey": self.conf.deer_key,
        }
        url = "https://api2.pushdeer.com/message/push"
        if self.conf.deer_url:
            url = self.conf.deer_url

        response = await httpx_client.post(url, data=data)
        response_data = response.json()

        if len(response_data.get("content").get("result")) > 0:
            self.logger.info("PushDeer 推送成功！")
        else:
            self.logger.error(f"PushDeer 推送失败！错误信息：{response_data}")

    async def chat(self, title: str, content: str) -> None:
        """
        通过Chat 推送消息
        """
        if not self.conf.chat_url or not self.conf.chat_token:
            return
        self.logger.info("chat 服务启动")
        data = "payload=" + json.dumps({"text": title + "\n" + content})
        url = self.conf.chat_url + self.conf.chat_token

        response = await httpx_client.post(url, data=data)

        if response.status_code == 200:
            self.logger.info("Chat 推送成功！")
        else:
            self.logger.error(f"Chat 推送失败！错误信息：{response}")

    async def pushplus_bot(self, title: str, content: str) -> None:
        """
        通过 pushplus 推送消息。
        """
        if not self.conf.push_plus_token:
            return
        self.logger.info("PUSHPLUS 服务启动")

        url = "https://www.pushplus.plus/send"
        data = {
            "token": self.conf.push_plus_token,
            "title": title,
            "content": content,
            "topic": self.conf.push_plus_user,
            "template": self.conf.push_plus_template,
            "channel": self.conf.push_plus_channel,
            "webhook": self.conf.push_plus_webhook,
            "callbackUrl": self.conf.push_plus_callbackurl,
            "to": self.conf.push_plus_to,
        }
        body = json.dumps(data).encode(encoding="utf-8")
        headers = {"Content-Type": "application/json"}
        response = await httpx_client.post(url=url, data=body, headers=headers)
        response_data = response.json()

        code = response_data["code"]
        if code == 200:
            self.logger.info("PUSHPLUS 推送请求成功，可根据流水号查询推送结果:" + response_data["data"])
            self.logger.info(
                "注意：请求成功并不代表推送成功，如未收到消息，请到pushplus官网使用流水号查询推送最终结果"
            )
        elif code == 900 or code == 903 or code == 905 or code == 999:
            self.logger.error(response_data["msg"])

        else:
            url_old = "http://pushplus.hxtrip.com/send"
            headers["Accept"] = "application/json"
            response = await httpx_client.post(url=url_old, data=body, headers=headers)
            response_data = response.json()

            if response_data["code"] == 200:
                self.logger.info("PUSHPLUS(hxtrip) 推送成功！")

            else:
                self.logger.error("PUSHPLUS 推送失败！")

    async def weplus_bot(self, title: str, content: str) -> None:
        """
        通过 微加机器人 推送消息。
        """
        if not self.conf.we_plus_bot_token:
            return
        self.logger.info("微加机器人 服务启动")

        template = "txt"
        if len(content) > 800:
            template = "html"

        url = "https://www.weplusbot.com/send"
        data = {
            "token": self.conf.we_plus_bot_token,
            "title": title,
            "content": content,
            "template": template,
            "receiver": self.conf.we_plus_bot_receiver,
            "version": self.conf.we_plus_bot_version,
        }
        body = json.dumps(data).encode(encoding="utf-8")
        headers = {"Content-Type": "application/json"}
        response = await httpx_client.post(url=url, data=body, headers=headers)
        response_data = response.json()

        if response_data["code"] == 200:
            self.logger.info("微加机器人 推送成功！")
        else:
            self.logger.error("微加机器人 推送失败！")

    async def qmsg_bot(self, title: str, content: str) -> None:
        """
        使用 qmsg 推送消息。
        """
        if not self.conf.qmsg_key or not self.conf.qmsg_type:
            return
        self.logger.info("qmsg 服务启动")

        url = f'https://qmsg.zendee.cn/{self.conf.qmsg_type}/{self.conf.qmsg_key}'
        payload = {"msg": f'{title}\n\n{content.replace("----", "-")}'.encode("utf-8")}
        response = await httpx_client.post(url=url, params=payload)
        response_data = response.json()

        if response_data["code"] == 0:
            self.logger.info("qmsg 推送成功！")
        else:
            self.logger.error(f'qmsg 推送失败！{response_data["reason"]}')

    async def wecom_app(self, title: str, content: str) -> None:
        """
        通过 企业微信 APP 推送消息。
        """
        if not self.conf.qywx_am:
            return
        QYWX_AM_AY = re.split(",", self.conf.qywx_am)
        if 4 < len(QYWX_AM_AY) > 5:
            self.logger.error("QYWX_AM 设置错误!!")
            return
        self.logger.info("企业微信 APP 服务启动")

        # 如果没有配置 media_id 默认就以 text 方式发送
        try:
            media_id = QYWX_AM_AY[4]
        except IndexError:
            media_id = ""
        wx = WeCom(self.conf)
        if not media_id:
            message = title + "\n\n" + content
            response = await wx.send_text(message)
        else:
            response = await wx.send_mpnews(title, content, media_id)

        if response == "ok":
            self.logger.info("企业微信推送成功！")
        else:
            self.logger.error(f"企业微信推送失败！错误信息如下：\n{response}")

    async def wecom_bot(self, title: str, content: str) -> None:
        """
        通过 企业微信机器人 推送消息。
        """
        if not self.conf.qywx_key:
            return
        self.logger.info("企业微信机器人服务启动")

        origin = "https://qyapi.weixin.qq.com"
        if self.conf.qywx_origin:
            origin = self.conf.qywx_origin

        url = f"{origin}/cgi-bin/webhook/send?key={self.conf.qywx_key}"
        headers = {"Content-Type": "application/json;charset=utf-8"}
        data = {"msgtype": "text", "text": {"content": f"{title}\n\n{content}"}}

        response = await httpx_client.post(
            url=url, data=json.dumps(data), headers=headers, timeout=15
        )
        response_data = response.json()

        if response_data["errcode"] == 0:
            self.logger.info("企业微信机器人推送成功！")
        else:
            self.logger.error("企业微信机器人推送失败！")

    async def telegram_bot(self, title: str, content: str) -> None:
        """
        使用 telegram 机器人 推送消息。
        """
        if not self.conf.tg_bot_token or not self.conf.tg_user_id:
            return
        self.logger.info("tg 服务启动")

        if self.conf.tg_api_host:
            url = f"{self.conf.tg_api_host}/bot{self.conf.tg_bot_token}/sendMessage"
        else:
            url = (
                f"https://api.telegram.org/bot{self.conf.tg_bot_token}/sendMessage"
            )
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        payload = {
            "chat_id": str(self.conf.tg_user_id),
            "text": f"{title}\n\n{content}",
            "disable_web_page_preview": "true",
        }
        proxies = None
        if self.conf.tg_proxy_host and self.conf.tg_proxy_port:
            tg_proxy_auth = self.conf.tg_proxy_auth
            tg_proxy_host = self.conf.tg_proxy_host
            if tg_proxy_auth is not None and "@" not in tg_proxy_host:
                tg_proxy_host = (
                        tg_proxy_auth
                        + "@"
                        + tg_proxy_host
                )
            proxyStr = "http://{}:{}".format(
                tg_proxy_host, self.conf.tg_proxy_port
            )
            proxies = {"http": proxyStr, "https": proxyStr}

        response = await httpx_client.post(
            url=url, headers=headers, params=payload, proxies=proxies
        )
        response_data = response.json()

        if response_data["ok"]:
            self.logger.info("tg 推送成功！")
        else:
            self.logger.error("tg 推送失败！")

    async def aibotk(self, title: str, content: str) -> None:
        """
        使用 智能微秘书 推送消息。
        """
        if (
                not self.conf.aibotk_key
                or not self.conf.aibotk_type
                or not self.conf.aibotk_name
        ):
            return
        self.logger.info("智能微秘书 服务启动")

        if self.conf.aibotk_type == "room":
            url = "https://api-bot.aibotk.com/openapi/v1/chat/room"
            data = {
                "apiKey": self.conf.aibotk_key,
                "roomName": self.conf.aibotk_name,
                "message": {"type": 1, "content": f"【青龙快讯】\n\n{title}\n{content}"},
            }
        else:
            url = "https://api-bot.aibotk.com/openapi/v1/chat/contact"
            data = {
                "apiKey": self.conf.aibotk_key,
                "name": self.conf.aibotk_name,
                "message": {"type": 1, "content": f"【青龙快讯】\n\n{title}\n{content}"},
            }
        body = json.dumps(data).encode(encoding="utf-8")
        headers = {"Content-Type": "application/json"}

        response = await httpx_client.post(url=url, data=body, headers=headers)
        response_data = response.json()
        self.logger.debug(response_data)
        if response_data["code"] == 0:
            self.logger.info("智能微秘书 推送成功！")
        else:
            self.logger.error(f'智能微秘书 推送失败！{response_data["error"]}')

    def smtp(self, title: str, content: str) -> None:
        """
        使用 SMTP 邮件 推送消息。
        """
        if (
                not self.conf.smtp_server
                or not self.conf.smtp_ssl
                or not self.conf.smtp_email
                or not self.conf.smtp_password
                or not self.conf.smtp_name
        ):
            return
        self.logger.info("SMTP 邮件 服务启动")

        message = MIMEText(content, "plain", "utf-8")
        message["From"] = formataddr(
            (
                Header(self.conf.smtp_name, "utf-8").encode(),
                self.conf.smtp_email,
            )
        )
        message["To"] = formataddr(
            (
                Header(self.conf.smtp_name, "utf-8").encode(),
                self.conf.smtp_email,
            )
        )
        message["Subject"] = Header(title, "utf-8")

        try:
            smtp_server_conn = (
                smtplib.SMTP_SSL(self.conf.smtp_server)
                if self.conf.smtp_ssl == "true"
                else smtplib.SMTP(self.conf.smtp_server)
            )
            smtp_server_conn.login(
                self.conf.smtp_email, self.conf.smtp_password
            )
            smtp_server_conn.sendmail(
                self.conf.smtp_email,
                self.conf.smtp_email,
                message.as_bytes(),
            )
            smtp_server_conn.close()
            self.logger.info("SMTP 邮件 推送成功！")
        except Exception as e:
            self.logger.error(f"SMTP 邮件 推送失败！{e}")

    async def pushme(self, title: str, content: str) -> None:
        """
        使用 PushMe 推送消息。
        """
        if not self.conf.pushme_key:
            return
        self.logger.info("PushMe 服务启动")

        url = (
            self.conf.pushme_url
            if self.conf.pushme_url
            else "https://push.i-i.me/"
        )
        data = {
            "push_key": self.conf.pushme_key,
            "title": title,
            "content": content,
            "date": "",  # 从配置中获取日期
            "type": "",  # 从配置中获取类型
        }

        response = await httpx_client.post(url, data=data)

        if response.status_code == 200 and response.text == "success":
            self.logger.info("PushMe 推送成功！")
        else:
            self.logger.error(f"PushMe 推送失败！{response.status_code} {response.text}")

    async def chronocat(self, title: str, content: str) -> None:
        """
        使用 CHRONOCAT 推送消息。
        """
        if (
                not self.conf.chronocat_url
                or not self.conf.chronocat_qq
                or not self.conf.chronocat_token
        ):
            return

        self.logger.info("CHRONOCAT 服务启动")

        user_ids = re.findall(r"user_id=(\d+)", self.conf.chronocat_qq)
        group_ids = re.findall(r"group_id=(\d+)", self.conf.chronocat_qq)

        url = f'{self.conf.chronocat_url}/api/message/send'
        headers = {
            "Content-Type": "application/json",
            "Authorization": f'Bearer {self.conf.chronocat_token}',
        }

        for chat_type, ids in [(1, user_ids), (2, group_ids)]:
            if not ids:
                continue
            for chat_id in ids:
                data = {
                    "peer": {"chatType": chat_type, "peerUin": chat_id},
                    "elements": [
                        {
                            "elementType": 1,
                            "textElement": {"content": f"{title}\n\n{content}"},
                        }
                    ],
                }
                response = await httpx_client.post(url, headers=headers, data=json.dumps(data))
                if response.status_code == 200:
                    if chat_type == 1:
                        self.logger.info(f"QQ个人消息:{ids}推送成功！")
                    else:
                        self.logger.info(f"QQ群消息:{ids}推送成功！")
                else:
                    if chat_type == 1:
                        self.logger.error(f"QQ个人消息:{ids}推送失败！")
                    else:
                        self.logger.error(f"QQ群消息:{ids}推送失败！")

    async def ntfy(self, title: str, content: str) -> None:
        """
        通过 Ntfy 推送消息
        """

        def encode_rfc2047(text: str) -> str:
            """将文本编码为符合 RFC 2047 标准的格式"""
            encoded_bytes = base64.b64encode(text.encode("utf-8"))
            encoded_str = encoded_bytes.decode("utf-8")
            return f"=?utf-8?B?{encoded_str}?="

        if not self.conf.ntfy_topic:
            return
        self.logger.info("ntfy 服务启动")
        priority = "3"
        if not self.conf.ntfy_priority:
            self.logger.warning("ntfy 服务的NTFY_PRIORITY 未设置!!默认设置为3")
        else:
            priority = self.conf.ntfy_priority

        # 使用 RFC 2047 编码 title
        encoded_title = encode_rfc2047(title)

        data = content.encode(encoding="utf-8")
        headers = {"Title": encoded_title, "Priority": priority,
                   "Icon": "https://qn.whyour.cn/logo.png"}  # 使用编码后的 title
        if self.conf.ntfy_token:
            headers['Authorization'] = "Bearer " + self.conf.ntfy_token
        else:
            if self.conf.ntfy_username and self.conf.ntfy_password:
                authStr = self.conf.ntfy_username + ":" + self.conf.ntfy_password
                headers['Authorization'] = "Basic " + base64.b64encode(authStr.encode('utf-8')).decode('utf-8')
        if self.conf.ntfy_actions:
            headers['Actions'] = encode_rfc2047(self.conf.ntfy_actions)

        url = self.conf.ntfy_url + "/" + self.conf.ntfy_topic

        response = await httpx_client.post(url, data=data, headers=headers)
        if response.status_code == 200:  # 使用 response.status_code 进行检查
            self.logger.info("Ntfy 推送成功！")
        else:
            self.logger.error(f"Ntfy 推送失败！错误信息：{response.text}")

    async def wxpusher_bot(self, title: str, content: str) -> None:
        """
        通过 wxpusher 推送消息。
        支持的环境变量:
        - WXPUSHER_APP_TOKEN: appToken
        - WXPUSHER_TOPIC_IDS: 主题ID, 多个用英文分号;分隔
        - WXPUSHER_UIDS: 用户ID, 多个用英文分号;分隔
        """
        if not self.conf.wxpusher_app_token:
            return

        url = "https://wxpusher.zjiecode.com/api/send/message"

        # 处理topic_ids和uids，将分号分隔的字符串转为数组
        topic_ids = []
        if self.conf.wxpusher_topic_ids:
            topic_ids = [
                int(id.strip())
                for id in self.conf.wxpusher_topic_ids.split(";")
                if id.strip()
            ]

        uids = []
        if self.conf.wxpusher_uids:
            uids = [
                uid.strip()
                for uid in self.conf.wxpusher_uids.split(";")
                if uid.strip()
            ]

        # topic_ids uids 至少有一个
        if not topic_ids and not uids:
            self.logger.error("wxpusher 服务的 WXPUSHER_TOPIC_IDS 和 WXPUSHER_UIDS 至少设置一个!!")
            return

        self.logger.info("wxpusher 服务启动")

        data = {
            "appToken": self.conf.wxpusher_app_token,
            "content": f"<h1>{title}</h1><br/><div style='white-space: pre-wrap;'>{content}</div>",
            "summary": title,
            "contentType": 2,
            "topicIds": topic_ids,
            "uids": uids,
            "verifyPayType": 0,
        }

        headers = {"Content-Type": "application/json"}

        response = await httpx_client.post(url=url, json=data, headers=headers)
        response_data = response.json()

        if response_data.get("code") == 1000:
            self.logger.info("wxpusher 推送成功！")
        else:
            self.logger.error(f"wxpusher 推送失败！错误信息：{response_data.get('msg')}")

    def get_available_methods(self):
        """
        获取所有可用的推送方法名称
        """
        methods = []
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if not name.startswith('_') and name != 'get_available_methods' and name != 'send':
                methods.append(name)
        return methods

    async def send(self, title: str, content: str):
        """
        根据配置发送消息到所有启用的推送渠道
        """
        if not content:
            self.logger.warning(f"{title} 推送内容为空！")
            return

        content += "\n\n" + await one() if self.conf.hitokoto else ""

        # 获取所有推送方法
        methods = self.get_available_methods()

        # 创建任务列表
        tasks = []

        # 根据配置决定启用哪些推送方式
        for method_name in methods:
            # 检查是否启用了该推送方式
            should_send = False

            # 根据不同的推送方式检查启用条件
            if method_name == "bark" and self.conf.bark_push:
                should_send = True
            elif method_name == "console" and hasattr(self.conf, 'console') and self.conf.console:
                should_send = True
            elif method_name == "dingding_bot" and self.conf.dd_bot_token and self.conf.dd_bot_secret:
                should_send = True
            elif method_name == "feishu_bot" and self.conf.fskey:
                should_send = True
            elif method_name == "go_cqhttp" and self.conf.gobot_url and self.conf.gobot_qq:
                should_send = True
            elif method_name == "gotify" and self.conf.gotify_url and self.conf.gotify_token:
                should_send = True
            elif method_name == "iGot" and self.conf.igot_push_key:
                should_send = True
            elif method_name == "serverJ" and self.conf.push_key:
                should_send = True
            elif method_name == "pushdeer" and self.conf.deer_key:
                should_send = True
            elif method_name == "chat" and self.conf.chat_url and self.conf.chat_token:
                should_send = True
            elif method_name == "pushplus_bot" and self.conf.push_plus_token:
                should_send = True
            elif method_name == "weplus_bot" and self.conf.we_plus_bot_token:
                should_send = True
            elif method_name == "qmsg_bot" and self.conf.qmsg_key and self.conf.qmsg_type:
                should_send = True
            elif method_name == "wecom_app" and self.conf.qywx_am:
                should_send = True
            elif method_name == "wecom_bot" and self.conf.qywx_key:
                should_send = True
            elif method_name == "telegram_bot" and self.conf.tg_bot_token and self.conf.tg_user_id:
                should_send = True
            elif method_name == "aibotk" and self.conf.aibotk_key and self.conf.aibotk_type and self.conf.aibotk_name:
                should_send = True
            elif method_name == "smtp" and self.conf.smtp_server and self.conf.smtp_ssl and self.conf.smtp_email and self.conf.smtp_password and self.conf.smtp_name:
                # SMTP是同步方法，直接调用
                if should_send:
                    self.smtp(title, content)
                continue
            elif method_name == "pushme" and self.conf.pushme_key:
                should_send = True
            elif method_name == "chronocat" and self.conf.chronocat_url and self.conf.chronocat_qq and self.conf.chronocat_token:
                should_send = True
            elif method_name == "ntfy" and self.conf.ntfy_topic:
                should_send = True
            elif method_name == "wxpusher_bot" and self.conf.wxpusher_app_token and (
                    self.conf.wxpusher_topic_ids or self.conf.wxpusher_uids):
                should_send = True

            # 如果启用了该推送方式，则添加到任务列表
            if should_send:
                method = getattr(self, method_name)
                if inspect.iscoroutinefunction(method):
                    tasks.append(method(title, content))
                else:
                    # 对于同步方法，使用线程运行
                    tasks.append(asyncio.get_event_loop().run_in_executor(None, method, title, content))

        # 并行执行所有推送任务
        if tasks:
            await asyncio.gather(*tasks)
        else:
            raise ValueError(f"无推送渠道，请检查通知变量是否正确")


class WeCom:
    def __init__(self, conf: NotificationConfig):
        self.conf = conf
        self.CORPID = None
        self.CORPSECRET = None
        self.AGENTID = None
        self.ORIGIN = "https://qyapi.weixin.qq.com"
        if conf.qywx_origin:
            self.ORIGIN = conf.qywx_origin

    async def get_access_token(self):
        if self.conf.qywx_am:
            QYWX_AM_AY = re.split(",", self.conf.qywx_am)
            if len(QYWX_AM_AY) >= 2:
                self.CORPID = QYWX_AM_AY[0]
                self.CORPSECRET = QYWX_AM_AY[1]

        url = f"{self.ORIGIN}/cgi-bin/gettoken"
        values = {
            "corpid": self.CORPID,
            "corpsecret": self.CORPSECRET,
        }

        req = await httpx_client.post(url, params=values)
        data = json.loads(req.text)
        return data["access_token"]

    async def send_text(self, message, touser="@all"):
        QYWX_AM_AY = re.split(",", self.conf.qywx_am)
        if len(QYWX_AM_AY) >= 4:
            touser = QYWX_AM_AY[2]
            self.AGENTID = QYWX_AM_AY[3]

        send_url = (
            f"{self.ORIGIN}/cgi-bin/message/send?access_token={await self.get_access_token()}"
        )
        send_values = {
            "touser": touser,
            "msgtype": "text",
            "agentid": self.AGENTID,
            "text": {"content": message},
            "safe": "0",
        }
        send_msges = bytes(json.dumps(send_values), "utf-8")

        respone = await httpx_client.post(send_url, data=send_msges)
        respone = respone.json()
        return respone["errmsg"]

    async def send_mpnews(self, title, message, media_id, touser="@all"):
        QYWX_AM_AY = re.split(",", self.conf.qywx_am)
        if len(QYWX_AM_AY) >= 4:
            touser = QYWX_AM_AY[2]
            self.AGENTID = QYWX_AM_AY[3]

        send_url = (
            f"{self.ORIGIN}/cgi-bin/message/send?access_token={await self.get_access_token()}"
        )
        send_values = {
            "touser": touser,
            "msgtype": "mpnews",
            "agentid": self.AGENTID,
            "mpnews": {
                "articles": [
                    {
                        "title": title,
                        "thumb_media_id": media_id,
                        "author": "Author",
                        "content_source_url": "",
                        "content": message.replace("\n", "<br/>"),
                        "digest": message,
                    }
                ]
            },
        }
        send_msges = bytes(json.dumps(send_values), "utf-8")

        respone = await httpx_client.post(send_url, data=send_msges)
        respone = respone.json()
        return respone["errmsg"]


async def one() -> str:
    """
    获取一条一言。
    :return:
    """
    url = "https://v1.hitokoto.cn/"

    res = await httpx_client.get(
        url=url,
    )
    res = res.json()
    return res.get('hitokoto', '') + "    ----" + res.get("from", '')


async def send(title: str, content: str, conf: NotificationConfig, **kwargs):
    """
    发送推送消息的全局函数接口
    """
    service = PushMessageService(conf)
    await service.send(title, content)
