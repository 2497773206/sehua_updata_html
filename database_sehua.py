import asyncio
import json
import re
import shelve
import sys
from pprint import pprint
from typing import List, Dict, Tuple

import aiohttp
import jinja2

from lxml import etree


def gen_pattern(lst):
    re_lst = [".*?" + i + ".*?" for i in lst]
    pattern = "(" + ")|(".join(re_lst) + ")"
    return pattern


class HtmlParseError(Exception):
    pass


class DailyUpdate:
    def __init__(
        self,
        platform_name: str,
        page_num: int,
        sub_list: List,
        hosts: Dict,
        sem_num: int,
    ):
        self.headers: Dict = {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/webp,*/*;q=0.8"
            ),
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": (
                "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2"
            ),
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 13_2_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.0.3 Mobile/15E148 Safari/604.1"
            ),
        }
        self.hosts: Dict[str, str] = hosts
        self.platforms: Dict[str, Dict] = {
            "sehua": {
                "platform": "sehua",
                "database": "sehua_title_id",
                "template": "sehua_base.html",
                "base_url": self.hosts["sehua"] + "/forum.php?mod=forumdisplay&fid={}&page={}&mobile=2",
                "framework": "discuz",
            },
        }
        if platform_name not in self.platforms:
            raise TypeError("Unkown Platform!")
        else:
            self.platform: Dict[str, str] = self.platforms[platform_name]

        self.page_num: int = page_num
        self.sub_list: List[int] = sub_list
        self.sem: asyncio.Semaphore = asyncio.Semaphore(sem_num)
        self.all_threads: List[Tuple] = []
        self.authors_threads: List[Tuple] = []
        self.match_threads: List[Tuple] = []
        self.match2_threads: List[Tuple] = []
        self.new_threads: List[Tuple] = []

    async def get_one_page(
        self, sub_num: int, page: int, session: aiohttp.ClientSession
    ) -> str:
        base_url = self.platform["base_url"]
        async with self.sem:
            async with session.get(
                base_url.format(sub_num, page), headers=self.headers
            ) as resp:
                text = await resp.text(encoding="utf-8")
                return text

    async def get_all_pages(self) -> None:
        async with aiohttp.ClientSession() as session:
            tasks = []
            for sub_num in self.sub_list:
                tasks.extend(
                    [
                        self.get_one_page(sub_num, page, session)
                        for page in range(1, self.page_num + 1)
                    ]
                )

            for rslt in asyncio.as_completed(tasks):
                text = await rslt
                try:
                    self.get_all_threads(text)
                except IndexError:
                    raise HtmlParseError(
                        "Invalid html page, please retry later"
                    )

    def get_all_threads(self, text: str) -> None:
        platf_n = self.platform["platform"]
        discuz_thread_ptn = {
            "sehua": r"tid=(\d+)",
        }
        t_ptn = discuz_thread_ptn[platf_n]
        self._get_threads_discuz(text, platf_n, t_ptn)

    def _get_threads_discuz(
        self, text: str, platf_n: str, thread_ptn: str
    ) -> None:
        html = etree.HTML(text.encode('utf-8'))
        lst = html.xpath("//*/div[@class='n5_htnrys cl']")
        for item in lst:
            url_path = item.xpath('.//a[1]/@href')[0]
            url_path = ''.join(url_path)
            if url_path == "https://utnqn.com":
                pass
            else:
                href = self.hosts[platf_n] + "/" + url_path#链接
                t_id = re.findall(thread_ptn, url_path)#页面id
                t_id = ''.join(t_id)
                title = item.xpath('.//h1/a/text()')#标题
                title = ''.join(title)
                img_src = item.xpath('.//a[1]/img/@data-original')#图片
                img_src = ''.join(img_src)
                author = "匿名"
                thread_tp = (t_id, title, href, img_src, author)
                print(thread_tp)
                self.all_threads.append(thread_tp)

    def keyword_filter(self) -> None:
        for thread_tp in self.all_threads:
            title, author = thread_tp[1], thread_tp[-1]
            self.match_threads.append(thread_tp)
            self.match2_threads.append(thread_tp)
            self.authors_threads.append(thread_tp)

    def get_new_threads(self) -> None:
        database = self.platform["database"]
        with shelve.open(database) as db:
            for tp in (
                self.match_threads + self.match2_threads + self.authors_threads
            ):
                if not db.get(str(tp[0])):
                    db[str(tp[0])] = tp[1]
                    self.new_threads.append(tp)
        print("\n @@  platform:", self.platform["platform"])
        #pprint([[tp[1], tp[2]] for tp in self.new_threads])

    def generate_html(self) -> None:
        template_loader = jinja2.FileSystemLoader(searchpath="./")
        template_env = jinja2.Environment(loader=template_loader)
        template_file = self.platform["template"]
        template = template_env.get_template(template_file)
        output_text = template.render(
            include_rslt=self.match_threads,
            include_2nd_rslt=self.match2_threads,
            new_daily=self.new_threads,
            fav_auths_lst=self.authors_threads,
        )

        with open(f"/www/wwwroot/121.5.227.248/{self.platform['platform']}.html", "w", encoding='utf-8') as fh:#网页存放位置
            fh.write(output_text)

    def run(self) -> None:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self.get_all_pages())
        loop.close()

        self.keyword_filter()
        self.get_new_threads()
        self.generate_html()


if __name__ == "__main__":
    with open("config.json", "r", encoding='utf-8') as file:
        config = json.load(file)
    page_num_dict = config.get("page_num_dict")
    subs_dict = config.get("subs_dict")
    hosts_dict = config.get("hosts_dict")
    concur_num_dict = config.get("concur_num_dict")

    platform = sys.argv[1]
    assert platform in ["sehua"]

    pages = page_num_dict[platform]
    concur_num = concur_num_dict[platform]
    subs = subs_dict[platform]
    daily_update = DailyUpdate(
        platform_name=platform,
        page_num=pages,
        sub_list=subs,
        hosts=hosts_dict,
        sem_num=concur_num
    )

    daily_update.run()
