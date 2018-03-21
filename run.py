import shutil
import requests
import time
import re
from slackclient import SlackClient
import logging
import toml
from plugins.tooter import Tooter
from plugins.tweeter import Tweeter


class Twitoot(object):

    def __init__(self, config_path='config.toml', secret_path='secret.toml', log_level_console=logging.INFO, log_level_file=logging.DEBUG, log_file_name='default.log'):
        # 設定ファイル
        self.CONFIG = toml.load(open(config_path, encoding='utf-8'))
        self.SECRET = toml.load(open(secret_path, encoding='utf-8'))

        # 認証後に代入する
        self.bot_id = None

        # ログの設定(ログはコンソールに表示する)
        logging.getLogger().setLevel(logging.DEBUG)
        self.formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

        self.ch = logging.StreamHandler()
        self.ch.setLevel(log_level_console)
        self.ch.setFormatter(self.formatter)
        logging.getLogger().addHandler(self.ch)

        self.fh = logging.FileHandler(log_file_name, mode='a', encoding=None, delay=False)
        self.fh.setLevel(log_level_file)
        self.fh.setFormatter(self.formatter)
        logging.getLogger().addHandler(self.fh)

        self.sc = SlackClient(self.SECRET['slack']['api_token'])

    def start(self):
        restart_count = 0
        while restart_count < self.CONFIG['system']['restart_max']:
            try:
                logging.info('restart count:' + str(restart_count))
                restart_count += 1
                if self.sc.rtm_connect():
                    logging.info('Bot connected and running!')
                    self.bot_id = self.sc.api_call("auth.test")["user_id"]  # botのuser IDを取得する
                    while True:  # コマンド待ち
                        cmd, channel, file_info = self._parse_slack_cmd(self.sc.rtm_read())
                        if cmd and channel and not file_info:  # 画像なしメンション
                            logging.info('Handled: mention txt  -> ' + str(self._handle_command(cmd, channel)))
                        elif cmd and channel and file_info:  # 画像付きメンション
                            logging.info('Handled: mention img txt  -> '
                                         + str(self._handle_command_with_img(cmd, channel, file_info)))
                        time.sleep(self.CONFIG['system']['rtm_interval'])
                else:
                    logging.warning('Connection failed.')
            except Exception as e:
                # たまに`Connection is already closed.`エラーが出るのでその時にrestart
                logging.error('Error detected : ' + str(e))
        logging.critical('Quit: Restart count exceeds the restart_max value.')

    def _handle_cmd_sns(self, cmd, img_path):

        # TODO: 構造が汚い！
        mastodon1 = {"server": self.SECRET['mastodon']['server_1']['url'],
                     "client_key": self.SECRET['mastodon']['server_1']['app_1']['client_key'],
                     "client_secret": self.SECRET['mastodon']['server_1']['app_1']['client_secret'],
                     "access_token": self.SECRET['mastodon']['server_1']['app_1']['id_1']['access_token']}

        twitter1 = {"consumer_key": self.SECRET['twitter']['app_1']['consumer_key'],
                    "consumer_secret": self.SECRET['twitter']['app_1']['consumer_secret'],
                    "access_token": self.SECRET['twitter']['app_1']['id_1']['access_token'],
                    "access_token_secret": self.SECRET['twitter']['app_1']['id_1']['access_token_secret']}

        logging.info('Handling cmd SNS: ' + cmd + ',' + str(img_path))
        return self._tweet(twitter1, cmd, img_path) + '\n' + self._toot(mastodon1, cmd, img_path)

    def _tweet(self, twitter_id, text, img_list):
        """
        plugins.tweeterを呼び出すメソッド
        :param twitter_id: twitter token情報を格納したdict
        :param text: tweetするtext
        :param img_list: メディアへのpath(e.g. `tmp/abc.png`)のlist or None
        :return: ログ(string)
        """
        logging.info('Tweeting: ' + text + ',' + str(img_list))
        is_success, result = Tweeter.tweet_by_id(twitter_id, text, img_list)
        return self.CONFIG['bot']['res_tweet'] + ':' + str(is_success)

    def _toot(self, mastodon_id, text, img_list):
        """
        plugins.tooterを呼び出すメソッド
        :param mastodon_id: mastodon token情報を格納したdict
        :param text: tootするtext
        :param img_list: メディアへのpath(e.g. `tmp/abc.png`)のlist or None
        :return: ログ(string)
        """
        logging.info('Tooting: ' + text + ',' + str(img_list))
        is_success, result = Tooter.toot_by_id(mastodon_id, text, img_list)
        return self.CONFIG['bot']['res_toot'] + ':' + str(is_success)

    def _download_img(self, img_info):
        # TODO: Windowsではscraping中に`UnicodeEncodeError: 'cp932' codec can't encode character '\u25e0' ...`エラーが出る

        img_id = img_info[0]
        img_name = img_info[1]

        # public urlを発行する
        resp_pub = self.sc.api_call('files.sharedPublicURL', token=self.SECRET['slack']['files_api_token'], file=img_id)
        logging.info('sharedPublicURL for ' + str(img_id))

        # public urlのtextからfileのurlを抽出する
        url_pub = resp_pub['file']['permalink_public']
        res_pub = requests.get(url_pub, stream=True)
        url_file = re.compile(r'<img src="https://.*').findall(res_pub.text)[0][10:-2]

        # 抽出したfileのurlからファイルをダウンロードする
        res_file = requests.get(url_file, stream=True)
        save_path = self.CONFIG['system']['path_tmp'] + img_name
        with open(save_path, "wb") as fp:
            shutil.copyfileobj(res_file.raw, fp)

        # public urlをrevokeする
        resp_depub = self.sc.api_call('files.revokePublicURL', token=self.SECRET['slack']['files_api_token'], file=img_id)
        logging.info('revokePublicURL for ' + str(img_id))

        return save_path

    def _handle_cmd_kill(self, cmd):
        """
        killコマンドの処理を行うメソッド
        :param cmd: botにメンションで送ったメッセージ
        :return: Slackに投稿するメッセージ
        """
        cmds = cmd.split()
        if len(cmds) > 1:
            if cmds[1] in ['you', 'お前']:
                return self.CONFIG['bot']['res_kill_you']
        return self.CONFIG['bot']['res_kill']

    def _handle_command_with_img(self, cmd, channel, img_info):
        logging.info('Handling img cmd: ' + str(img_info) + ',' + cmd)
        img_path = self._download_img(img_info)
        logging.info('File saved: ' + str(img_info) + '->' + img_path)

        response = self.CONFIG['bot']['res_img_default']

        if cmd.startswith(self.CONFIG['bot']['cmd_sns']):
            response = self._handle_cmd_sns(cmd[len(self.CONFIG['bot']['cmd_sns']) + 1:], img_path)  # cmdの"しゃべる "以降のみ
        return self.sc.api_call('chat.postMessage', channel=channel, text=response, as_user=True)

    def _handle_command(self, cmd, channel) -> dict:
        """
        botに対するメンションを処理するメソッド, file_url=Noneの場合のコマンドの識別および処理の実施を行う
        :param cmd: コマンド(botに対するメンション)
        :param channel: Slackの対象チャンネル
        :return: サーバの応答(Slackの対象チャンネルにコマンドの実行結果を投稿した結果)
        """
        response = self.CONFIG['bot']['res_default']
        logging.info('Handling cmd: ' + cmd)
        if cmd.startswith(('help', '-h', '--help', '使い方', 'a')):
            response = self.CONFIG['bot']['res_help']
        elif cmd.startswith('kill'):
            response = self._handle_cmd_kill(cmd)
        elif cmd.startswith(self.CONFIG['bot']['cmd_sns']):  # cmdの"しゃべる "以降のみ
            response = self._handle_cmd_sns(cmd[len(self.CONFIG['bot']['cmd_sns']) + 1:], None)  # 画像無しSNS投稿

        return self.sc.api_call('chat.postMessage', channel=channel, text=response, as_user=True)

    def _parse_slack_cmd(self, cmd) -> (str, str, list):
        """
        RTM(Real Time Messaging) APIで受け取ったメッセージをパースするメソッド, botに対するメンションのみを認識する
        :param cmd: RTMで受け取ったJSON
        :return: (botに対するメンション, 対象チャンネル, [ファイルID, ファイル名]|None)
        """
        at_bot = '<@' + self.bot_id + '>'
        for elem in cmd:
            logging.debug("Parsing: " + str(cmd))
            if ('text' in elem) and (at_bot in elem['text']):  # cmd[text]の中を見て、botに対するメンションの場合のみ
                if len(elem['text'].split('uploaded a file: <')) > 1:  # ファイルアップロードを検知
                    if elem['file']['name'].split('.')[-1].lower() in ['png', 'jpg', 'jpeg', 'bmp', 'gif']:  # 画像を検知
                        logging.info("Parsing: mention img txt")
                        file_info = [elem['file']['id'], elem['file']['name']]  # file_info[0]: ID, file_info[1]: name
                        logging.debug("file_info: " + str(file_info))
                        logging.debug('say (with an img): ' + elem['text'].split(at_bot)[1].strip())
                        return elem['text'].split(at_bot)[1].strip(), elem['channel'], file_info
                else:
                    logging.info("Parsing: mention txt")
                    return elem['text'].split(at_bot)[1].strip(), elem['channel'], None
        return None, None, None


if __name__ == '__main__':
    Twitoot().start()
