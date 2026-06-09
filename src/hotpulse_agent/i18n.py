from __future__ import annotations


TEXT_ZH = {
    "Officials confirmed the Harbor Bridge shuttle crash injured 14 passengers.": "官方确认 Harbor Bridge 接驳车事故造成 14 名乘客受伤。",
    "Officials said no fatalities had been confirmed as of the first briefing.": "官方在首次通报时表示，尚未确认有人员死亡。",
    "A brake-system inspection started after the crash.": "事故发生后，制动系统检查已经启动。",
    "Harbor General Hospital said two crash victims remained in surgery.": "Harbor General Hospital 表示，两名事故伤者仍在接受手术。",
    "The hospital said all admitted patients were alive at the time of the update.": "医院表示，截至该次更新，所有收治患者均仍在世。",
    "Witnesses disputed whether the shuttle accelerated before impact.": "目击者对接驳车撞击前是否加速存在不同说法。",
    "Authorities had not confirmed the vehicle speed.": "主管部门尚未确认车辆当时速度。",
    "The transit regulator denied rumors that fatalities had been confirmed.": "交通监管机构否认了“已确认死亡”的网络传言。",
    "The investigation remained active.": "相关调查仍在进行中。",
}


SOURCE_TYPE_ZH = {
    "official": "官方",
    "media": "媒体",
    "web": "网页",
    "local": "本地语料",
}


def zh_text(text: str) -> str:
    return TEXT_ZH.get(text, text)


def zh_source_type(source_type: str) -> str:
    return SOURCE_TYPE_ZH.get(source_type, source_type)
