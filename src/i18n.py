"""Internationalisation module.

Structure mirrors InniUpdaterChin's ``app.i18n`` (a ``_Translator`` with a
``t(key)`` lookup). ``_STRINGS`` below is the source of truth: every English
string the app can display, plus the hand-checked translations for Chinese,
English, French and Spanish (German and Japanese are partially checked).

Everything else comes from ``assets/i18n/<code>.json`` — one flat
``{string_id: text}`` catalog per language, generated from the English strings
by ``scripts/i18n_fill.py`` with Google Translate, which is what makes the app
usable in the ~250 languages Google can translate English into. A catalog is
read the first time its language is used, so shipping them all costs nothing at
startup. ``tr()`` prefers the catalog, then the tables here, then English.
"""

import json
import logging
import os
import string
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# The languages whose copy is reviewed by hand (see the module docstring).
# English comes in three: the international table, which claims no country, and
# the two written dialects that differ in spelling; each of the latter is the
# international table plus its own overrides, so a new string only has to be
# written once.
_SUPPORTED = ("zh-CN", "en", "en-GB", "en-US", "de", "fr", "es", "ja")

_STRINGS: dict = {
    # The name is the language's own word for "Updater" (アップデーター,
    # 更新程序, Programa de actualización); "CE" stays in English only, because
    # a translated "Community Edition" appended to a translated "Updater" turns
    # the title into a mouthful. Copy that would rather not repeat a long name
    # says "this app" in that language instead. Catalogs carry their own
    # spelling under the same key.
    "app_name": {
        "zh-CN": "更新程序",
        "en": "Updater CE",
        "fr": "Programme de mise à jour",
        "es": "Actualizador",
        "de": "Updater",
        "ja": "アップデーター",
    },
    "app_title": {
        "zh-CN": "刷机升级",
        "en": "Flash Upgrade",
        "fr": "Mise à jour du firmware",
        "es": "Actualización de firmware",
    },
    "author_by": {
        "en": "by",
        "zh-CN": "作者",
        "fr": "par",
        "es": "por",
        "de": "von",
        "ja": "作",
    },
    "brand_author_by": {
        "en": "by {author}",
        "zh-CN": "作者：{author}",
        "fr": "par {author}",
        "es": "por {author}",
        "de": "von {author}",
        "ja": "作：{author}",
    },

    # Navigation
    "nav_section_main": {"zh-CN": "导航", "en": "Navigation", "fr": "Navigation", "es": "Navegación"},
    "nav_section_tools": {"zh-CN": "工具", "en": "Tools", "fr": "Outils", "es": "Herramientas"},
    "nav_home": {"zh-CN": "首页", "en": "Home", "fr": "Accueil", "es": "Inicio"},
    "nav_select_package": {
        "zh-CN": "选择软件",
        "en": "Select Software",
        "fr": "Choisir un logiciel",
        "es": "Seleccionar software",
    },
    "nav_install_package": {
        "zh-CN": "安装软件",
        "en": "Install Software",
        "fr": "Installer le logiciel",
        "es": "Instalar software",
    },
    "nav_flash": {
        "zh-CN": "开始刷机",
        "en": "Flash",
        "fr": "Flashing",
        "es": "Flashing",
    },
    "nav_log": {
        "zh-CN": "诊断",
        "en": "Diagnostics",
        "fr": "Diagnostics",
        "es": "Diagnóstico",
        "de": "Diagnose",
        "ja": "診断",
    },
    "nav_install_output": {
        "zh-CN": "诊断",
        "en": "Diagnostics",
        "fr": "Diagnostics",
        "es": "Diagnóstico",
        "de": "Diagnose",
        "ja": "診断",
    },
    "install_output_title": {
        "zh-CN": "安装输出",
        "en": "Install output",
        "fr": "Sortie de l'installation",
        "es": "Salida de la instalación",
        "de": "Installationsausgabe",
        "ja": "インストール出力",
    },
    "install_output_empty": {
        "zh-CN": "尚无输出。安装开始后，这里会显示工具的终端内容。",
        "en": "No output yet. Terminal lines from the install tool will appear here.",
        "fr": "Pas encore de sortie. Les lignes du terminal de l'outil d'installation apparaîtront ici.",
        "es": "Aún no hay salida. Aquí aparecerán las líneas del terminal de la herramienta de instalación.",
        "de": "Noch keine Ausgabe. Terminalzeilen des Installationstools erscheinen hier.",
        "ja": "まだ出力はありません。インストールツールの端末出力がここに表示されます。",
    },
    "install_done_dismiss": {
        "zh-CN": "完成",
        "en": "Done",
        "fr": "Terminé",
        "es": "Listo",
        "de": "Fertig",
        "ja": "完了",
    },
    "nav_language": {
        "zh-CN": "语言",
        "en": "Language",
        "fr": "Langue",
        "es": "Idioma",
    },
    "nav_section_navigation": {
        "zh-CN": "导航",
        "en": "Navigation",
        "fr": "Navigation",
        "es": "Navegación",
    },
    "nav_section_tools": {
        "zh-CN": "工具",
        "en": "Tools",
        "fr": "Outils",
        "es": "Herramientas",
    },
    "nav_credits": {
        "zh-CN": "致谢",
        "en": "Credits / Thanks",
        "fr": "Crédits / Remerciements",
        "es": "Créditos / Agradecimientos",
    },
    "nav_donate": {
        "zh-CN": "支持我们",
        "en": "Support Us",
        "fr": "Nous soutenir",
        "es": "Apóyanos",
    },
    "nav_check_updates": {
        "zh-CN": "应用更新",
        "en": "App Updates",
        "fr": "Mises à jour de l'app",
        "es": "Actualizaciones de la app",
        "de": "App-Updates",
        "ja": "アプリのアップデート",
    },
    "nav_linux_setup": {
        "zh-CN": "Linux 环境配置",
        "en": "Linux Setup",
        "fr": "Configuration Linux",
        "es": "Configuración de Linux",
    },
    # The tool's own name, in every language: the rail row has to stay short,
    # and translating "GUI" made it long enough to elide.
    "nav_sp_flash_tool_gui": {
        "zh-CN": "SP Flash Tool",
        "en": "SP Flash Tool",
        "fr": "SP Flash Tool",
        "es": "SP Flash Tool",
        "de": "SP Flash Tool",
        "ja": "SP Flash Tool",
    },
    "nav_get_update": {
        "zh-CN": "获取 {version}",
        "en": "Get {version}",
        "fr": "Obtenir {version}",
        "es": "Obtener {version}",
        "de": "{version} holen",
        "ja": "{version} を取得",
    },
    "nav_settings": {
        "zh-CN": "设置",
        "en": "Settings",
        "fr": "Paramètres",
        "es": "Configuración",
    },
    "linux_setup_title": {
        "zh-CN": "Linux 刷机环境与 SP Flash Tool 配置",
        "en": "Linux Flashing & SP Flash Tool Setup",
        "fr": "Configuration Linux & SP Flash Tool",
        "es": "Configuración de Linux & SP Flash Tool",
    },
    "linux_setup_auto_btn": {
        "zh-CN": "一键自动配置 (pkexec)",
        "en": "Configure System (pkexec)",
        "fr": "Configurer le système (pkexec)",
        "es": "Configurar sistema (pkexec)",
    },
    "linux_setup_copy_cmd": {
        "zh-CN": "复制终端命令",
        "en": "Copy Terminal Command",
        "fr": "Copier la commande",
        "es": "Copiar comando",
    },
    "linux_setup_recheck": {
        "zh-CN": "重新检查环境",
        "en": "Re-check Readiness",
        "fr": "Revérifier l'état",
        "es": "Volver a comprobar",
    },
    "linux_setup_welcome": {
        "zh-CN": "首次运行：配置 Linux 刷机环境",
        "en": "Setting Up SP Flash Tool for Linux",
        "fr": "Configuration de SP Flash Tool pour Linux",
        "es": "Configuración de SP Flash Tool para Linux",
    },
    "linux_setup_welcome_desc": {
        "zh-CN": "正在为您下载并配置官方 MediaTek 刷机工具，以实现高速稳定刷机。",
        "en": "Downloading and configuring the high-speed flashing engine so you can restore and update your player effortlessly.",
        "fr": "Téléchargement et configuration du moteur de flash haute vitesse pour votre lecteur.",
        "es": "Descargando y configurando el motor de flasheo de alta velocidad para su reproductor.",
    },
    "linux_card_engine_title": {
        "zh-CN": "刷机引擎 (SP Flash Tool)",
        "en": "Flashing Engine (SP Flash Tool)",
        "fr": "Moteur de flash (SP Flash Tool)",
        "es": "Motor de flasheo (SP Flash Tool)",
    },
    "linux_card_engine_desc": {
        "zh-CN": "官方 MediaTek 底层刷机工具，已集成跨发行版兼容运行库。",
        "en": "MediaTek native flashing tool with universal library compatibility staged.",
        "fr": "Outil de flash natif MediaTek avec compatibilité universelle.",
        "es": "Herramienta nativa de flasheo MediaTek con compatibilidad universal.",
    },
    "linux_card_usb_title": {
        "zh-CN": "USB 设备权限 (udev)",
        "en": "USB Device Permissions (udev)",
        "fr": "Permissions USB (udev)",
        "es": "Permisos USB (udev)",
    },
    "linux_card_usb_desc": {
        "zh-CN": "允许普通用户直接与 Bootloader 通信，无需 root 权限。",
        "en": "Grants desktop permission to communicate with the player in Bootloader mode.",
        "fr": "Autorise la communication avec le lecteur en mode Bootloader sans root.",
        "es": "Permite la comunicación con el reproductor en modo Bootloader sin root.",
    },
    "linux_card_system_title": {
        "zh-CN": "系统运行环境",
        "en": "System Environment",
        "fr": "Environnement système",
        "es": "Entorno del sistema",
    },
    "linux_card_grant_btn": {
        "zh-CN": "授权 USB 访问 (一键配置)",
        "en": "Grant USB Access (1-Click)",
        "fr": "Accorder l'accès USB (1-Clic)",
        "es": "Conceder acceso USB (1-Clic)",
    },
    "linux_card_granted_badge": {
        "zh-CN": "已配置",
        "en": "Configured",
        "fr": "Configuré",
        "es": "Configurado",
    },
    "linux_card_ready_badge": {
        "zh-CN": "就绪",
        "en": "Ready",
        "fr": "Prêt",
        "es": "Listo",
    },
    "linux_card_downloading_badge": {
        "zh-CN": "正在下载...",
        "en": "Downloading...",
        "fr": "Téléchargement...",
        "es": "Descargando...",
    },
    "linux_card_action_badge": {
        "zh-CN": "需要授权",
        "en": "Action Needed",
        "fr": "Action requise",
        "es": "Acción requerida",
    },
    "linux_setup_done_btn": {
        "zh-CN": "完成并进入工具",
        "en": "Continue to Updater",
        "fr": "Continuer vers l'outil",
        "es": "Continuar al actualizador",
    },
    "linux_setup_view_log": {
        "zh-CN": "显示技术详情",
        "en": "Show Technical Details",
        "fr": "Afficher les détails techniques",
        "es": "Mostrar detalles técnicos",
    },
    "linux_setup_hide_log": {
        "zh-CN": "隐藏技术详情",
        "en": "Hide Technical Details",
        "fr": "Masquer les détails techniques",
        "es": "Ocultar detalles técnicos",
    },
    "system_checker_title": {
        "zh-CN": "SP Flash Tool 系统检查与诊断",
        "en": "SP Flash Tool System Checker & Diagnostics",
        "fr": "Vérificateur de système SP Flash Tool & Diagnostics",
        "es": "Comprobador de sistema SP Flash Tool y Diagnóstico",
    },
    "system_checker_desc": {
        "zh-CN": "全面验证 Linux 发行版的驱动、内核模块、用户权限与依赖项，确保 SP Flash Tool 稳定运行。",
        "en": "Comprehensive verification of drivers, kernel modules, permissions, and runtime libraries to ensure reliable flashing.",
        "fr": "Vérification complète des pilotes, modules de noyau, permissions et bibliothèques pour un flash fiable.",
        "es": "Verificación completa de controladores, módulos del kernel, permisos y bibliotecas para un flasheo confiable.",
    },
    "system_checker_run_btn": {
        "zh-CN": "准备电脑",
        "en": "Prepare your computer",
        "fr": "Préparer votre ordinateur",
        "es": "Preparar el equipo",
    },
    "system_checker_run_btn_installer": {
        "zh-CN": "准备设备连接",
        "en": "Prepare device connection",
        "fr": "Préparer la connexion de l’appareil",
        "es": "Preparar la conexión del dispositivo",
    },
    "settings_checker_group_installer": {
        "zh-CN": "设备连接",
        "en": "Device connection",
        "fr": "Connexion de l’appareil",
        "es": "Conexión del dispositivo",
    },
    "settings_checker_desc_installer": {
        "zh-CN": "让电脑能够连接播放器以安装软件更新。",
        "en": "Gets your computer ready to connect to your player for software updates.",
        "fr": "Prépare votre ordinateur à se connecter au lecteur pour les mises à jour logicielles.",
        "es": "Prepara el equipo para conectarse al reproductor y actualizar el software.",
    },
    "system_checker_run_btn_advanced": {
        "zh-CN": "准备 SP Flash Tool",
        "en": "Prepare SP Flash Tool",
        "fr": "Préparer SP Flash Tool",
        "es": "Preparar SP Flash Tool",
    },
    "settings_checker_group_advanced": {
        "zh-CN": "SP Flash Tool 诊断与硬件检查",
        "en": "SP Flash Tool Diagnostics & Hardware",
        "fr": "Diagnostics SP Flash Tool & matériel",
        "es": "Diagnóstico y hardware de SP Flash Tool",
    },
    "settings_checker_desc_advanced": {
        "zh-CN": "准备刷机工具，检查 USB 访问、串口权限与系统组件。",
        "en": "Checks the advanced Linux setup for SP Flash Tool, USB access, serial permissions, and system components.",
        "fr": "Prépare l'outil de flashage et vérifie l'accès USB, les permissions série et les composants système.",
        "es": "Prepara la herramienta y comprueba el acceso USB, permisos serie y componentes del sistema.",
    },
    "settings_checker_desc_friendly": {
        "zh-CN": "让电脑能够连接播放器并可靠地安装软件更新。",
        "en": "Gets your computer ready to connect to your player and install software updates reliably.",
        "fr": "Prépare votre ordinateur à se connecter au lecteur et à installer les mises à jour de façon fiable.",
        "es": "Prepara el equipo para conectarse al reproductor e instalar actualizaciones de forma fiable.",
    },
    "settings_checker_desc": {
        "zh-CN": "让电脑能够连接播放器并可靠地安装软件更新。",
        "en": "Gets your computer ready to connect to your player and install software updates reliably.",
        "fr": "Prépare votre ordinateur à se connecter au lecteur et à installer les mises à jour de façon fiable.",
        "es": "Prepara el equipo para conectarse al reproductor e instalar actualizaciones de forma fiable.",
    },
    "linux_prep_starting": {
        "zh-CN": "正在准备 SP Flash Tool…",
        "en": "Preparing SP Flash Tool…",
        "fr": "Préparation de SP Flash Tool…",
        "es": "Preparando SP Flash Tool…",
    },
    "linux_prep_starting_friendly": {
        "zh-CN": "正在准备设备连接…",
        "en": "Preparing your computer…",
        "fr": "Préparation de votre ordinateur…",
        "es": "Preparando el equipo…",
    },
    "linux_prep_ready_friendly": {
        "zh-CN": "电脑已准备好连接播放器。",
        "en": "Your computer is ready to connect to your player.",
        "fr": "Votre ordinateur est prêt à se connecter au lecteur.",
        "es": "El equipo está listo para conectarse al reproductor.",
    },
    "linux_prep_ready": {
        "zh-CN": "就绪 — SP Flash Tool 已准备好。",
        "en": "Ready — SP Flash Tool is prepared.",
        "fr": "Prêt — SP Flash Tool est préparé.",
        "es": "Listo: SP Flash Tool está preparado.",
    },
    "linux_prep_connected_friendly": {
        "zh-CN": "已发现播放器，正在更新连接权限…",
        "en": "Player found — refreshing connection access…",
        "fr": "Lecteur détecté — actualisation des droits de connexion…",
        "es": "Reproductor detectado: actualizando el acceso de conexión…",
    },
    "linux_prep_not_connected_friendly": {
        "zh-CN": "稍后连接播放器即可。",
        "en": "You can connect your player when asked.",
        "fr": "Vous pourrez connecter le lecteur lorsque cela vous sera demandé.",
        "es": "Puedes conectar el reproductor cuando se te indique.",
    },
    "linux_prep_blocked": {
        "zh-CN": "此电脑无法运行 SP Flash Tool。",
        "en": "This computer cannot run SP Flash Tool.",
        "fr": "Cet ordinateur ne peut pas exécuter SP Flash Tool.",
        "es": "Este equipo no puede ejecutar SP Flash Tool.",
    },
    "linux_prep_fix_body": {
        "zh-CN": "还需要一次管理员授权：MediaTek 设备的 USB 规则与串口访问权限。",
        "en": "Your password is needed once to set up this computer for flashing: the USB rules for MediaTek devices and serial port access.",
        "fr": "Votre mot de passe est demandé une fois pour préparer cet ordinateur au flashage : les règles USB des appareils MediaTek et l'accès au port série.",
        "es": "Se necesita tu contraseña una vez para preparar este equipo para el flasheo: las reglas USB para dispositivos MediaTek y el acceso al puerto serie.",
    },
    "linux_prep_fix_btn": {
        "zh-CN": "授权",
        "en": "Allow Access",
        "fr": "Autoriser",
        "es": "Permitir acceso",
    },
    "linux_prep_needs_access": {
        "zh-CN": "未获得授权 — 刷机需要此权限。",
        "en": "Access was not granted — flashing needs it.",
        "fr": "Accès non accordé — le flashage en a besoin.",
        "es": "No se concedió el acceso; el flasheo lo necesita.",
    },
    "linux_prep_fixing": {
        "zh-CN": "正在应用…",
        "en": "Applying…",
        "fr": "Application…",
        "es": "Aplicando…",
    },
    "linux_prep_fixed": {
        "zh-CN": "已授权 — SP Flash Tool 已就绪。",
        "en": "Access granted — SP Flash Tool is ready.",
        "fr": "Accès accordé — SP Flash Tool est prêt.",
        "es": "Acceso concedido: SP Flash Tool está listo.",
    },
    "linux_prep_fixed_friendly": {
        "zh-CN": "连接权限已准备好。",
        "en": "Connection access is ready.",
        "fr": "L’accès à la connexion est prêt.",
        "es": "El acceso de conexión está listo.",
    },
    "settings_checker_group": {
        "zh-CN": "准备电脑",
        "en": "Prepare your computer",
        "fr": "Préparer votre ordinateur",
        "es": "Preparar el equipo",
    },
    "settings_checker_desc_friendly": {
        "zh-CN": "让电脑能够连接播放器并可靠地安装软件更新。",
        "en": "Gets your computer ready to connect to your player and install software updates reliably.",
        "fr": "Prépare votre ordinateur à se connecter au lecteur et à installer les mises à jour de façon fiable.",
        "es": "Prepara el equipo para conectarse al reproductor e instalar actualizaciones de forma fiable.",
    },
    "settings_checker_desc": {
        "zh-CN": "让电脑能够连接播放器并可靠地安装软件更新。",
        "en": "Gets your computer ready to connect to your player and install software updates reliably.",
        "fr": "Prépare votre ordinateur à se connecter au lecteur et à installer les mises à jour de façon fiable.",
        "es": "Prepara el equipo para conectarse al reproductor e instalar actualizaciones de forma fiable.",
    },
    "settings_driver_group": {
        "zh-CN": "联发科 USB 驱动",
        "en": "MediaTek USB Driver",
        "fr": "Pilote USB MediaTek",
        "es": "Controlador USB MediaTek",
    },
    "settings_driver_desc": {
        "zh-CN": "首次使用本应用前，请先安装联发科 USB 驱动，然后重启电脑。",
        "en": "Before using the app for the first time, you must install the MediaTek USB driver, then reboot your computer.",
        "fr": "Avant la première utilisation de l'application, vous devez installer le pilote USB MediaTek, puis redémarrer votre ordinateur.",
        "es": "Antes de usar la aplicación por primera vez, debe instalar el controlador USB de MediaTek y luego reiniciar el ordenador.",
    },
    "settings_download_drivers_btn": {
        "zh-CN": "下载驱动",
        "en": "Download Drivers",
        "fr": "Télécharger les pilotes",
        "es": "Descargar controladores",
    },
    "system_checker_autofix_btn": {
        "zh-CN": "一键修复权限与系统配置",
        "en": "Auto-Fix Permissions & Setup System",
        "fr": "Corriger automatiquement les autorisations",
        "es": "Reparar permisos automáticamente",
    },
    "system_checker_launch_gui_btn": {
        "zh-CN": "启动 SP Flash Tool GUI",
        "en": "Launch SP Flash Tool GUI",
        "fr": "Lancer SP Flash Tool GUI",
        "es": "Iniciar SP Flash Tool GUI",
    },

    # Home
    "home_desc": {
        "zh-CN": "在线查找固件，或选择本地刷机包，将固件刷入您的设备。",
        "en": "Find firmware online or select a local package, then flash it to your device.",
        "fr": "Trouvez le firmware en ligne ou sélectionnez un paquet local, puis flashez-le sur votre appareil.",
        "es": "Encuentra firmware en línea o selecciona un paquete local y flashea tu dispositivo.",
    },
    "home_flow_title": {
        "zh-CN": "标准流程",
        "en": "Standard Flow",
        "fr": "Procédure standard",
        "es": "Flujo estándar",
    },
    "home_step_1": {
        "zh-CN": "选择设备型号",
        "en": "Choose your device model",
        "fr": "Choisissez le modèle de votre appareil",
        "es": "Elige el modelo de tu dispositivo",
    },
    "home_step_2": {
        "zh-CN": "选择软件并下载",
        "en": "Choose software and download",
        "fr": "Choisissez le logiciel et téléchargez",
        "es": "Elige el software y descárgalo",
    },
    "home_step_3": {
        "zh-CN": "等待设备连接",
        "en": "Wait for device connection",
        "fr": "Attendez la connexion de l'appareil",
        "es": "Espera la conexión del dispositivo",
    },
    "home_step_4": {
        "zh-CN": "关机并连接 USB",
        "en": "Power off and connect USB",
        "fr": "Éteignez et connectez en USB",
        "es": "Apágalo y conéctalo por USB",
    },
    "home_step_5": {
        "zh-CN": "识别即刷",
        "en": "Detect and flash",
        "fr": "Détection et flashing",
        "es": "Detectar y flashear",
    },
    "home_step_6": {
        "zh-CN": "完成",
        "en": "Done",
        "fr": "Terminé",
        "es": "Listo",
    },
    "home_btn_select": {
        "zh-CN": "进入选择",
        "en": "Select Package",
        "fr": "Choisir un paquet",
        "es": "Seleccionar paquete",
    },
    "home_btn_flash": {
        "zh-CN": "进入刷机",
        "en": "Start Flash",
        "fr": "Démarrer le flashing",
        "es": "Iniciar flashing",
    },

    # Select package page
    "sel_title": {
        "zh-CN": "选择软件",
        "en": "Select Software",
        "fr": "Choisir un logiciel",
        "es": "Seleccionar software",
    },
    "sel_source_title": {
        "zh-CN": "软件包来源",
        "en": "Package Source",
        "fr": "Source du paquet",
        "es": "Origen del paquete",
    },
    "sel_source_desc": {
        "zh-CN": "支持在线下载固件，或导入本地刷机包。",
        "en": "Download firmware online, or import a local package.",
        "fr": "Téléchargez le firmware en ligne ou importez un paquet local.",
        "es": "Descarga firmware en línea o importa un paquete local.",
    },
    "sel_online": {
        "zh-CN": "在线下载",
        "en": "Online",
        "fr": "En ligne",
        "es": "En línea",
    },
    "sel_local": {
        "zh-CN": "本地文件",
        "en": "Local File",
        "fr": "Fichier local",
        "es": "Archivo local",
    },
    "sel_install_from_file": {
        "zh-CN": "从本地文件安装…",
        "en": "Install from a file…",
        "fr": "Installer à partir d'un fichier…",
        "es": "Instalar desde un archivo…",
    },
    "sel_install_online": {
        "zh-CN": "从列表安装…",
        "en": "Install from list…",
        "fr": "Installer depuis la liste…",
        "es": "Instalar desde la lista…",
    },
    "sel_model": {
        "zh-CN": "型号",
        "en": "Model",
        "fr": "Modèle",
        "es": "Modelo",
        "de": "Modell",
        "ja": "モデル",
    },
    "sel_software": {
        "zh-CN": "软件",
        "en": "Software",
        "fr": "Logiciel",
        "es": "Software",
    },
    "sel_type": {
        "zh-CN": "类型",
        "en": "Type",
        "fr": "Type",
        "es": "Tipo",
        "de": "Typ",
        "ja": "タイプ",
    },
    "sel_release": {
        "zh-CN": "版本",
        "en": "Release",
        "fr": "Version",
        "es": "Versión",
    },
    "settings_advanced_group": {
        "zh-CN": "高级设置",
        "en": "Advanced Settings",
        "fr": "Paramètres avancés",
        "es": "Configuración avanzada",
        "de": "Erweiterte Einstellungen",
        "ja": "詳細設定",
    },
    "settings_advanced_toggle": {
        "zh-CN": "显示高级安装选项",
        "en": "Show Advanced Settings",
        "fr": "Afficher les options d'installation avancées",
        "es": "Mostrar opciones avanzadas de instalación",
        "de": "Erweiterte Installationsoptionen anzeigen",
        "ja": "高度なインストールオプションを表示する",
    },
    "settings_advanced_desc": {
        "zh-CN": "显示底层刷机工具与终端安装模式。",
        "en": "Shows install method, installation mode and release filters.",
        "fr": "Afficher les outils de flashage sous-jacents et les modes terminal.",
        "es": "Mostrar herramientas de flasheo subyacentes y modos de terminal.",
        "de": "Low-Level-Flashtools und Terminal-Modi anzeigen.",
        "ja": "低レベルのフラッシュツールとターミナルモードを表示します。",
    },
    "settings_terminal_group": {
        "zh-CN": "安装方式",
        "en": "Installation Mode",
        "fr": "Mode d'installation",
        "es": "Modo de instalación",
    },
    "settings_guided_install": {
        "zh-CN": "在应用中引导我完成安装",
        "en": "Guide me through the install in the app",
        "fr": "Guidez-moi pendant l'installation dans l'app",
        "es": "Guíame durante la instalación en la app",
        "de": "Führe mich in der App durch die Installation",
        "ja": "アプリでインストール手順を案内する",
    },
    "settings_guided_install_desc": {
        "zh-CN": "应用会陪你完成关机、连接和安装。",
        "en": "The app stays with you through powering off, connecting, and installing.",
        "fr": "L'app vous accompagne pour éteindre, connecter et installer.",
        "es": "La app te acompaña al apagar, conectar e instalar.",
        "de": "Die App begleitet dich beim Ausschalten, Verbinden und Installieren.",
        "ja": "電源オフ、接続、インストールまでアプリが案内します。",
    },
    "settings_terminal_install": {
        "zh-CN": "在终端窗口中安装",
        "en": "Install from a terminal window",
        "fr": "Installer depuis une fenêtre de terminal",
        "es": "Instalar desde una ventana de terminal",
    },
    "settings_terminal_install_windows": {
        "zh-CN": "在命令提示符窗口中安装",
        "en": "Install in Command Prompt",
        "fr": "Installer dans l'Invite de commandes",
        "es": "Instalar en el Símbolo del sistema",
    },
    "settings_sp_gui_install": {
        "zh-CN": "在 SP Flash Tool 图形界面中安装",
        "en": "Install in SP Flash Tool GUI",
        "fr": "Installer dans l'interface graphique de SP Flash Tool",
        "es": "Instalar en la interfaz gráfica de SP Flash Tool",
    },
    "settings_sp_gui_desc": {
        "zh-CN": "关闭 更新程序 并打开 SP Flash Tool 图形界面，自动加载所选固件的 scatter 文件和镜像。",
        "en": "Closes Updater CE and opens SP Flash Tool GUI with the selected firmware scatter file and partition images preloaded.",
        "fr": "Ferme Programme de mise à jour et ouvre l'interface SP Flash Tool avec le fichier scatter et les images préchargés.",
        "es": "Cierra Actualizador y abre la GUI de SP Flash Tool con el archivo scatter y las imágenes precargadas.",
    },
    "settings_terminal_desc": {
        "zh-CN": "在终端中运行软件安装，让您可以看到安装进度的技术信息。",
        "en": "Runs your software installation in Terminal so you can see technical info on the progress.",
        "fr": "Exécute l'installation de votre logiciel dans le Terminal pour vous montrer les informations techniques de la progression.",
        "es": "Ejecuta la instalación de tu software en la Terminal para que veas la información técnica del progreso.",
    },
    "settings_terminal_desc_windows": {
        "zh-CN": "在命令提示符中运行软件安装，让您可以看到安装进度的技术信息。",
        "en": "Runs your software installation in Command Prompt so you can see technical info on the progress.",
        "fr": "Exécute l'installation de votre logiciel dans l'Invite de commandes pour vous montrer les informations techniques de la progression.",
        "es": "Ejecuta la instalación de tu software en el Símbolo del sistema para que veas la información técnica del progreso.",
    },
    "settings_sp_auth": {
        "zh-CN": "身份验证文件",
        "en": "Authentication File",
        "fr": "Fichier d'authentification",
        "es": "Archivo de autenticación",
    },
    "settings_sp_auth_browse": {
        "zh-CN": "选择…",
        "en": "Choose…",
        "fr": "Choisir…",
        "es": "Elegir…",
    },
    "settings_sp_auth_clear": {
        "zh-CN": "清除",
        "en": "Clear",
        "fr": "Effacer",
        "es": "Borrar",
    },
    "settings_sp_auth_none": {
        "zh-CN": "未设置",
        "en": "Not set",
        "fr": "Non défini",
        "es": "Sin definir",
    },
    "settings_sp_auth_desc": {
        "zh-CN": "可选。部分联发科 (MediaTek) 机型需要身份验证 (.auth) 文件才能被 SP Flash Tool 识别；如果您的设备需要，请在此添加。",
        "en": "Optional. Some MediaTek targets refuse to flash until SP Flash Tool is given an authentication (.auth) file; add yours here if the device needs one.",
        "fr": "Facultatif. Certaines cibles MediaTek refusent le flash tant que SP Flash Tool n'a pas reçu un fichier d'authentification (.auth) ; ajoutez le vôtre ici si votre appareil en a besoin.",
        "es": "Opcional. Algunos dispositivos MediaTek no se flashean hasta que SP Flash Tool recibe un archivo de autenticación (.auth); añade el tuyo aquí si tu dispositivo lo necesita.",
    },
    "settings_sp_auth_dialog": {
        "zh-CN": "选择身份验证文件",
        "en": "Select authentication file",
        "fr": "Sélectionner le fichier d'authentification",
        "es": "Seleccionar archivo de autenticación",
    },
    "settings_sp_auth_filter": {
        "zh-CN": "身份验证文件 (*.auth)",
        "en": "Authentication files (*.auth)",
        "fr": "Fichiers d'authentification (*.auth)",
        "es": "Archivos de autenticación (*.auth)",
    },
    "terminal_install_title": {
        "zh-CN": "终端安装",
        "en": "Terminal Install",
        "fr": "Installation en terminal",
        "es": "Instalación en terminal",
    },
    "terminal_install_no_scatter": {
        "zh-CN": "该固件包中未找到 scatter 文件，无法生成命令行命令。",
        "en": "No scatter file was found in this package, so no console command could be built.",
        "fr": "Aucun fichier scatter trouvé dans ce paquet : impossible de construire la commande console.",
        "es": "No se encontró ningún archivo scatter en este paquete, así que no se pudo crear el comando de consola.",
    },
    "terminal_install_no_tool": {
        "zh-CN": "当前安装方式没有可用的 SP Flash Tool 或 MTKClient 入口。",
        "en": "No SP Flash Tool build or MTKClient entry point is available for this method.",
        "fr": "Aucune version de SP Flash Tool ni point d'entrée MTKClient n'est disponible pour cette méthode.",
        "es": "No hay ninguna versión de SP Flash Tool ni punto de entrada de MTKClient para este método.",
    },
    "terminal_install_failed": {
        "zh-CN": "无法打开终端窗口。安装命令已写入：{path}",
        "en": "Could not open a terminal window. The install command was written to {path}.",
        "fr": "Impossible d'ouvrir une fenêtre de terminal. La commande a été écrite dans {path}.",
        "es": "No se pudo abrir una ventana de terminal. El comando se escribió en {path}.",
    },
    "status_terminal_install": {
        "zh-CN": "安装命令已在终端窗口中打开（{path}）。",
        "en": "Install command opened in your terminal window ({path}).",
        "fr": "Commande d'installation ouverte dans votre terminal ({path}).",
        "es": "Comando de instalación abierto en tu ventana de terminal ({path}).",
    },
    "terminal_install_prompt_title": {
        "zh-CN": "在{terminal_name}中安装",
        "en": "Install via {terminal_name}",
        "fr": "Installer via {terminal_name}",
        "es": "Instalar mediante {terminal_name}",
    },
    "terminal_install_prompt_body": {
        "zh-CN": "即将打开{terminal_name}运行 {method}。\n\n• 如果设备尚未关机，请关闭您的{device}。\n• 如果已连接 USB，请先断开连接。\n{hint}\n• {terminal_name}窗口打开后，将设备连接到电脑。\n\n点击“确定”将打开窗口并退出 更新程序。",
        "en": "{terminal_name} will now open running {method}.\n\n• If it isn't already off, power off your {device}.\n• If it is connected to USB, disconnect it first.\n{hint}\n• Connect your device to your computer when the {terminal_name} window has opened.\n\nClick OK to launch and exit Updater CE.",
        "fr": "{terminal_name} va s'ouvrir pour exécuter {method}.\n\n• Si l'appareil n'est pas déjà éteint, éteignez votre {device}.\n• S'il est connecté en USB, débranchez-le d'abord.\n{hint}\n• Connectez votre appareil à l'ordinateur lorsque la fenêtre {terminal_name} s'ouvrira.\n\nCliquez sur OK pour lancer et quitter Programme de mise à jour.",
        "es": "Se abrirá {terminal_name} ejecutando {method}.\n\n• Si no está apagado, apaga tu {device}.\n• Si está conectado al USB, desconéctalo primero.\n{hint}\n• Conecta tu dispositivo al ordenador cuando la ventana de {terminal_name} se haya abierto.\n\nHaz clic en Aceptar para iniciar y salir de Actualizador.",
    },
    "terminal_hint_innioasis": {
        "zh-CN": "• 提示：如果设备无法自动连接，请在插入 USB 线的同时，用回形针轻按复位孔中的重置按钮。",
        "en": "• Tip: If the device does not connect automatically, gently press the reset button in the pinhole with a paperclip while connecting the USB cable.",
        "fr": "• Astuce : si l'appareil ne se connecte pas automatiquement, appuyez doucement sur le bouton de réinitialisation avec un trombone tout en branchant le câble USB.",
        "es": "• Consejo: si el dispositivo no se conecta automáticamente, presiona suavemente el botón de reinicio con un clip mientras conectas el cable USB.",
    },
    "terminal_hint_generic": {
        "zh-CN": "• 提示：请将设备置于固件下载模式（因机型而异：例如取下电池并在保持取出状态下连接 USB，或在电池装入状态下关机连接，或在连接 USB 时按住音量加/减键）。",
        "en": "• Tip: Put your device in firmware download mode (varies by model: e.g. remove battery and keep it out with USB cable connected, or power off with battery in, or hold Volume Down/Up while connecting USB).",
        "fr": "• Astuce : mettez votre appareil en mode téléchargement de micrologiciel (selon le modèle : par ex. retirez la batterie en laissant le câble USB branché, ou éteignez avec la batterie, ou maintenez Volume Bas/Haut lors du branchement).",
        "es": "• Consejo: pon tu dispositivo en modo de descarga de firmware (varía según el modelo: p. ej. quita la batería y déjala fuera conectando el USB, o apaga con la batería puesta, o mantén pulsado Bajar/Subir Volumen al conectar el USB).",
    },
    "sp_gui_install_prompt_title": {
        "zh-CN": "在 SP Flash Tool 图形界面中安装",
        "en": "Install via SP Flash Tool GUI",
        "fr": "Installer via l'interface SP Flash Tool",
        "es": "Instalar mediante SP Flash Tool GUI",
    },
    "sp_gui_install_prompt_body": {
        "zh-CN": "SP Flash Tool 图形界面即将打开，并已预载固件。\n\n• 在 SP Flash Tool 下拉菜单中选择“Format All + Download”。\n• 点击“Download”按钮。\n• 请确保您的{device}已关机并断开 USB 连接。\n{hint}\n• 点击 Download 后，将 USB 线连接到电脑开始刷机。\n\n点击“确定”将启动 SP Flash Tool 并退出 更新程序。",
        "en": "SP Flash Tool GUI will now open with your firmware preloaded.\n\n• In SP Flash Tool, select \"Format All + Download\" from the dropdown box.\n• Click the \"Download\" button.\n• Make sure your {device} is powered off and disconnected.\n{hint}\n• Connect the USB cable to your computer after clicking Download.\n\nClick OK to launch SP Flash Tool and exit Updater CE.",
        "fr": "L'interface SP Flash Tool va s'ouvrir avec votre micrologiciel préchargé.\n\n• Dans SP Flash Tool, sélectionnez « Format All + Download » dans la liste déroulante.\n• Cliquez sur le bouton « Download ».\n• Assurez-vous que votre {device} est éteint et débranché.\n{hint}\n• Connectez le câble USB à l'ordinateur après avoir cliqué sur Download.\n\nCliquez sur OK pour lancer SP Flash Tool et quitter Programme de mise à jour.",
        "es": "La interfaz de SP Flash Tool se abrirá con el firmware precargado.\n\n• En SP Flash Tool, selecciona \"Format All + Download\" en el menú desplegable.\n• Haz clic en el botón \"Download\".\n• Asegúrate de que tu {device} esté apagado y desconectado.\n{hint}\n• Conecta el cable USB al ordenador después de hacer clic en Download.\n\nHaz clic en Aceptar para iniciar SP Flash Tool y salir de Actualizador.",
    },
    "settings_rockbox_group": {
        "zh-CN": "软件版本筛选",
        "en": "Release Filters",
        "fr": "Filtres de versions",
        "es": "Filtros de versiones",
    },
    "settings_rockbox_desc": {
        "zh-CN": "浏览软件列表时生效。",
        "en": "Applied when browsing software releases.",
        "fr": "Appliqué lors de la navigation des versions de logiciel.",
        "es": "Se aplica al explorar versiones de software.",
    },
    "settings_filter_original_pre307": {
        "zh-CN": "显示 3.0.7 之前的原版软件固件",
        "en": "Show Original Software releases older than 3.0.7",
        "fr": "Afficher les versions du logiciel d'origine antérieures à 3.0.7",
        "es": "Mostrar versiones del software original anteriores a 3.0.7",
    },
    "settings_original_pre307_warn_body": {
        "zh-CN": "3.0.7 之前的原版软件固件与 2026 年 3 月之后售出、预装 Innioasis OS 3.0.7 的 Y1 设备不兼容。\n\n在这些设备上刷入旧版本将导致无法开机，直到重新安装较新的版本。\n\n确定要显示 3.0.7 之前的原版软件固件吗？",
        "en": "Original Software releases older than 3.0.7 are not compatible with Y1 units sold with Innioasis OS 3.0.7. Flashing an older build on these units will prevent them from booting until a newer version is reinstalled.\n\nAre you sure you want to show Original Software releases older than 3.0.7?",
        "fr": "Les versions du logiciel d'origine antérieures à 3.0.7 ne sont pas compatibles avec les Y1 vendus avec Innioasis OS 3.0.7. Flasher une version plus ancienne empêchera ces appareils de démarrer jusqu'à la réinstallation d'une version plus récente.\n\nÊtes-vous sûr de vouloir afficher les versions du logiciel d'origine antérieures à 3.0.7 ?",
        "es": "Las versiones del software original anteriores a 3.0.7 no son compatibles con las unidades Y1 vendidas con Innioasis OS 3.0.7. Flashear una versión anterior impedirá que arranquen hasta que se reinstale una versión más nueva.\n\n¿Seguro que deseas mostrar versiones del software original anteriores a 3.0.7?",
    },
    "settings_filter_old_rockbox": {
        "zh-CN": "显示旧版 Rockbox 固件",
        "en": "Show Old Rockbox Builds",
        "fr": "Afficher les anciennes versions de Rockbox",
        "es": "Mostrar versiones antiguas de Rockbox",
    },
    "settings_filter_nightly": {
        "zh-CN": "显示每夜版与开发版",
        "en": "Show Nightly Dev Releases",
        "fr": "Afficher les versions Nightly et Dev",
        "es": "Mostrar versiones Nightly y de desarrollo",
    },
    "settings_filter_240p": {
        "zh-CN": "240p Rockbox 固件",
        "en": "240p Rockbox Releases",
        "fr": "Versions Rockbox 240p",
        "es": "Versiones de Rockbox 240p",
    },
    "settings_240p_tip": {
        "zh-CN": "显示 ROM 压缩包为 240p 变体（rom…_240p.zip）的固件。240p 固件不兼容 Innioasis OS 低于 3.0.7 的 Y1 设备，因此会同时启用“显示旧版 Rockbox 固件”。",
        "en": "Shows builds whose ROM zip is a 240p variant (rom\u2026_240p.zip). 240p builds are not compatible with Y1 units on Innioasis OS below 3.0.7, so Show Old Rockbox Builds is enabled with it.",
        "fr": "Affiche les versions dont le zip ROM est une variante 240p (rom\u2026_240p.zip). Les versions 240p ne sont pas compatibles avec les Y1 sous Innioasis OS antérieur à 3.0.7 : l'option des anciennes versions est donc activée en même temps.",
        "es": "Muestra versiones cuyo zip ROM es una variante 240p (rom\u2026_240p.zip). Las versiones 240p no son compatibles con unidades Y1 con Innioasis OS anterior a 3.0.7, por lo que se activa junto con la opción de versiones antiguas.",
    },
    "settings_old_rockbox_warn_title": {
        "zh-CN": "Rockbox 兼容性警告",
        "en": "Rockbox Compatibility Warning",
        "fr": "Avertissement de compatibilité Rockbox",
        "es": "Advertencia de compatibilidad de Rockbox",
    },
    "settings_old_rockbox_warn_body": {
        "zh-CN": "0.5 之前的 Rockbox-Y1 版本与 2026 年 3 月之后售出且预装 Innioasis OS 3.0.7 的 Y1 设备不兼容。\n\n在这些设备上刷入旧版本会导致无法开机。\n\n确定要显示旧版 Rockbox 固件吗？",
        "en": "Releases of Rockbox-Y1 before 0.5 are not compatible with Y1 units sold after March 2026 with Innioasis OS 3.0.7 pre-installed.\n\nFlashing an older build on these devices will prevent booting.\n\nAre you sure you want to show older Rockbox builds?",
        "fr": "Les versions de Rockbox-Y1 antérieures à 0.5 ne sont pas compatibles avec les appareils Y1 vendus après mars 2026 avec Innioasis OS 3.0.7 préinstallé.\n\nFlasher une ancienne version empêchera ces appareils de démarrer.\n\nÊtes-vous sûr de vouloir afficher les anciennes versions de Rockbox ?",
        "es": "Las versiones de Rockbox-Y1 anteriores a 0.5 no son compatibles con los dispositivos Y1 vendidos después de marzo de 2026 con Innioasis OS 3.0.7 preinstalado.\n\nFlashear una versión anterior impedirá que el dispositivo arranque.\n\n¿Seguro que deseas mostrar versiones anteriores de Rockbox?",
    },
    "sel_refresh": {
        "zh-CN": "刷新列表",
        "en": "Refresh",
        "fr": "Actualiser",
        "es": "Actualizar",
    },
    "sel_downloading": {
        "zh-CN": "正在下载…",
        "en": "Downloading…",
        "fr": "Téléchargement…",
        "es": "Descargando…",
    },
    "sel_download_progress": {
        "zh-CN": "下载进度",
        "en": "Download Progress",
        "fr": "Progression du téléchargement",
        "es": "Progreso de la descarga",
    },
    "sel_install": {
        "zh-CN": "安装 / 恢复",
        "en": "Install / Restore",
        "fr": "Installer / Restaurer",
        "es": "Instalar / Restaurar",
    },
    "sel_install_from_file": {
        "zh-CN": "从本地文件安装…",
        "en": "Install from a file…",
        "fr": "Installer depuis un fichier…",
        "es": "Instalar desde un archivo…",
    },
    "sel_back_to_online": {
        "zh-CN": "← 返回在线固件",
        "en": "← Back to Online System Software",
        "fr": "← Retour aux logiciels en ligne",
        "es": "← Volver al software en línea",
    },
    "sel_invalid_firmware_title": {
        "zh-CN": "需要固件包或 Scatter 文件",
        "en": "Firmware Package Required",
        "fr": "Paquet de firmware requis",
        "es": "Paquete de firmware requerido",
    },
    "sel_prompt_need_firmware": {
        "zh-CN": "请选择 .zip、.rar 压缩包或固件文件夹中的 scatter 文件（*scatter*.txt），以便在您的 {target} 上安装所选固件。",
        "en": "Please select a .zip, .rar archive, or a scatter file (*scatter*.txt) inside a firmware folder to install your chosen firmware on your {target}.",
        "fr": "Veuillez sélectionner une archive .zip, .rar ou un fichier scatter (*scatter*.txt) dans un dossier de firmware pour installer le firmware choisi sur votre {target}.",
        "es": "Seleccione un archivo .zip, .rar o un archivo scatter (*scatter*.txt) dentro de una carpeta de firmware para instalar el firmware elegido en su {target}.",
    },
    "player_generic": {
        "zh-CN": "播放器",
        "en": "player",
        "fr": "lecteur",
        "es": "reproductor",
    },
    "sel_browse": {
        "zh-CN": "浏览本地文件…",
        "en": "Browse Files…",
        "fr": "Parcourir…",
        "es": "Examinar…",
    },
    "sel_current_pkg": {
        "zh-CN": "当前软件包",
        "en": "Current Package",
        "fr": "Paquet actuel",
        "es": "Paquete actual",
    },
    "sel_status_title": {
        "zh-CN": "当前状态",
        "en": "Current Status",
        "fr": "État actuel",
        "es": "Estado actual",
    },
    "sel_placeholder": {
        "zh-CN": "请选择本地刷机包…",
        "en": "Select a firmware package…",
        "fr": "Sélectionnez un paquet firmware…",
        "es": "Selecciona un paquete de firmware…",
    },
    "sel_loading": {
        "zh-CN": "正在加载版本列表…",
        "en": "Loading releases…",
        "fr": "Chargement des versions…",
        "es": "Cargando versiones…",
    },
    "sel_offline": {
        "zh-CN": "当前无法获取在线列表，可使用本地文件，或检查网络后重试。",
        "en": "Online listings are unavailable right now. Use a local file, or check your connection.",
        "fr": "Les listes en ligne sont indisponibles pour le moment. Utilisez un fichier local ou vérifiez votre connexion.",
        "es": "Las listas en línea no están disponibles ahora. Usa un archivo local o revisa tu conexión.",
    },
    "sel_no_release": {
        "zh-CN": "该软件暂无可用版本",
        "en": "No releases available for this software",
        "fr": "Aucune version disponible pour ce logiciel",
        "es": "No hay versiones disponibles para este software",
    },
    "translate": {
        "zh-CN": "翻译",
        "en": "Translate",
        "fr": "Traduire",
        "es": "Traducir",
    },
    "translate_notes": {
        "zh-CN": "翻译更新说明",
        "en": "Translate",
        "fr": "Traduire les notes",
        "es": "Traducir notas",
    },
    "software_original": {
        "zh-CN": "原厂固件",
        "en": "Original Software",
        "fr": "Logiciel d'origine",
        "es": "Software original",
    },
    "translate_in_app": {
        "zh-CN": "应用内翻译",
        "en": "Translate in-app",
        "fr": "Traduire dans l'app",
        "es": "Traducir en la app",
    },
    "show_original": {
        "zh-CN": "显示原文",
        "en": "Show original",
        "fr": "Afficher l'original",
        "es": "Mostrar original",
    },
    "translated_with_google": {
        "zh-CN": "由 Google 翻译",
        "en": "Translated with Google",
        "fr": "Traduit avec Google",
        "es": "Traducido con Google",
    },
    "release_notes_loading": {
        "en": "Getting release notes…",
        "zh-CN": "正在获取更新说明…",
        "fr": "Récupération des notes de version…",
        "es": "Obteniendo notas de versión…",
        "de": "Versionshinweise werden geladen…",
        "ja": "リリースノートを取得中…",
    },
    "translating_notes": {
        "zh-CN": "正在翻译更新说明…",
        "en": "Translating release notes…",
        "fr": "Traduction des notes de version…",
        "es": "Traduciendo notas de versión…",
    },
    "open_in_google_translate": {
        "zh-CN": "在浏览器中查看",
        "en": "View in browser",
        "fr": "Voir dans le navigateur",
        "es": "Ver en el navegador",
    },
    "sel_dialog_title": {
        "zh-CN": "选择固件包",
        "en": "Select Firmware Package",
        "fr": "Sélectionner le paquet firmware",
        "es": "Seleccionar paquete de firmware",
    },
    "sel_browse_file": {
        "zh-CN": "选择文件…",
        "en": "Browse File…",
        "fr": "Parcourir fichier…",
        "es": "Examinar archivo…",
    },
    "sel_browse_folder": {
        "zh-CN": "选择文件夹…",
        "en": "Browse Folder…",
        "fr": "Parcourir dossier…",
        "es": "Examinar carpeta…",
    },
    "sel_dialog_folder_title": {
        "zh-CN": "选择固件文件夹",
        "en": "Select Firmware Folder",
        "fr": "Sélectionner le dossier du firmware",
        "es": "Seleccionar carpeta de firmware",
    },
    "sel_multiple_scatters_title": {
        "zh-CN": "检测到多个固件包",
        "en": "Multiple Firmware Packages Detected",
        "fr": "Plusieurs paquets de firmware détectés",
        "es": "Se detectaron varios paquetes de firmware",
    },
    "sel_multiple_scatters_error": {
        "zh-CN": "所选文件夹或压缩包中包含多个固件包或分散加载文件。请一次只选择一个固件压缩包或文件夹。",
        "en": "Multiple firmware packages or scatter files were found in this folder/archive. Please select a folder or archive containing only one firmware package at a time.",
        "fr": "Plusieurs paquets de firmware ou fichiers scatter ont été trouvés. Veuillez sélectionner un dossier ou une archive ne contenant qu'un seul firmware à la fois.",
        "es": "Se encontraron varios paquetes de firmware o archivos scatter. Por favor selecciona una carpeta o archivo que contenga solo un paquete de firmware a la vez.",
    },
    "app_name_mediatek_installer": {
        "zh-CN": "MediaTek 安装工具",
        "en": "MediaTek Installer",
        "fr": "Installateur MediaTek",
        "es": "Instalador MediaTek",
    },
    "app_name_mediatek_installer_short": {
        "zh-CN": "安装工具",
        "en": "Installer",
        "fr": "Installateur",
        "es": "Instalador",
    },
    "sp_auth_loaded": {
        "zh-CN": "使用身份验证文件：{path}",
        "en": "Using authentication file: {path}",
        "fr": "Fichier d'authentification utilisé : {path}",
        "es": "Usando archivo de autenticación: {path}",
    },
    "sp_search_restart": {
        "zh-CN": "尚未检测到设备，重新开始搜索（第 {count} 次）…",
        "en": "No device yet — searching again (attempt {count}). Keep the player powered off and connect it when prompted.",
        "fr": "Aucun appareil détecté — nouvelle recherche (tentative {count}). Laissez le lecteur éteint et branchez-le quand l'invite apparaît.",
        "es": "Aún no hay dispositivo — buscando de nuevo (intento {count}). Deja el reproductor apagado y conéctalo cuando se indique.",
    },
    "sp_auth_missing_file": {
        "zh-CN": "找不到身份验证文件（{path}）；将在没有它的情况下刷机。",
        "en": "Authentication file not found ({path}); flashing without it.",
        "fr": "Fichier d'authentification introuvable ({path}) ; flash sans celui-ci.",
        "es": "No se encontró el archivo de autenticación ({path}); se flasheará sin él.",
    },
    "sp_config_missing_platform": {
        "zh-CN": "scatter 文件未声明 platform，无法为身份验证文件生成控制台配置；将在没有身份验证的情况下刷机。",
        "en": "The scatter file does not declare its platform, so the console configuration for the authentication file could not be built; flashing without it.",
        "fr": "Le fichier scatter ne déclare pas sa plateforme ; la configuration console du fichier d'authentification n'a pas pu être créée ; flash sans celui-ci.",
        "es": "El archivo scatter no declara su plataforma, así que no se pudo crear la configuración de consola para el archivo de autenticación; se flasheará sin él.",
    },
    "sp_config_missing_storage": {
        "zh-CN": "scatter 文件未声明 storage（EMMC/NAND/UFS），无法为身份验证文件生成控制台配置；将在没有身份验证的情况下刷机。",
        "en": "The scatter file does not declare a storage type (EMMC/NAND/UFS), so the console configuration for the authentication file could not be built; flashing without it.",
        "fr": "Le fichier scatter ne déclare pas de type de stockage (EMMC/NAND/UFS) ; la configuration console du fichier d'authentification n'a pas pu être créée ; flash sans celui-ci.",
        "es": "El archivo scatter no declara un tipo de almacenamiento (EMMC/NAND/UFS), así que no se pudo crear la configuración de consola para el archivo de autenticación; se flasheará sin él.",
    },
    "sp_config_no_auth_file": {
        "zh-CN": "未提供身份验证文件；将在没有它的情况下刷机。",
        "en": "No authentication file was given; flashing without one.",
        "fr": "Aucun fichier d'authentification fourni ; flash sans celui-ci.",
        "es": "No se proporcionó ningún archivo de autenticación; se flasheará sin él.",
    },
    "sp_config_write_failed": {
        "zh-CN": "无法写入控制台配置文件；将在没有身份验证文件的情况下刷机。",
        "en": "Could not write the console configuration file; flashing without the authentication file.",
        "fr": "Impossible d'écrire le fichier de configuration console ; flash sans le fichier d'authentification.",
        "es": "No se pudo escribir el archivo de configuración de consola; se flasheará sin el archivo de autenticación.",
    },
    "settings_offline_mode_group": {
        "zh-CN": "离线模式",
        "en": "Offline Mode",
        "fr": "Mode hors ligne",
        "es": "Modo sin conexión",
    },
    "settings_offline_mode": {
        "zh-CN": "离线模式",
        "en": "Offline Mode",
        "fr": "Mode hors ligne",
        "es": "Modo sin conexión",
    },
    "settings_offline_mode_desc": {
        "zh-CN": "隐藏在线社区软件目录，仅使用本地软件文件进行安装。",
        "en": "Hide the online community catalogue and install software from local files only.",
        "en-US": "Hide the online community catalog and install software from local files only.",
        "fr": "Masque le catalogue communautaire en ligne et n'installe qu'à partir de fichiers locaux.",
        "es": "Oculta el catálogo comunitario en línea e instala software solo desde archivos locales.",
    },
    "sel_themepack_prompt_title": {
        "zh-CN": "安装 Rockbox 360p 主题包？",
        "en": "Install Rockbox 360p Theme Pack?",
        "fr": "Installer le pack de thèmes Rockbox 360p ?",
        "es": "¿Instalar paquete de temas Rockbox 360p?",
    },
    "sel_themepack_prompt_body": {
        "zh-CN": "您已完成 360p Rockbox 固件刷机。是否要立即为此设备安装 360p 精选主题包？",
        "en": "You have flashed a 360p Rockbox build. Would you like to install the 360p Theme Pack onto this device now?",
        "fr": "Vous avez flashé une version Rockbox 360p. Souhaitez-vous installer le pack de thèmes 360p maintenant ?",
        "es": "Has instalado una versión Rockbox 360p. ¿Deseas instalar el paquete de temas 360p ahora?",
    },
    "sel_themepack_guidance_title": {
        "zh-CN": "将播放器连接为 USB 存储模式",
        "en": "Connect Player in USB Storage Mode",
        "fr": "Connecter le lecteur en mode stockage USB",
        "es": "Conectar el reproductor en modo almacenamiento USB",
    },
    "sel_themepack_guidance_body": {
        "zh-CN": "请断开 USB 连接，开机启动一次 Rockbox（以便系统自动初始化 .rockbox 目录），然后重新插入 USB 并选择“USB 存储模式”。\n\n连接完成后，点击“继续”并选择播放器磁盘中的任意文件夹。",
        "en": "Please disconnect USB, power on and boot into Rockbox once (so it initializes its internal .rockbox system folder), then reconnect USB and select 'USB Storage Mode'.\n\nWhen connected, click Continue to select any folder on your player.",
        "fr": "Veuillez déconnecter l'USB, démarrer Rockbox une fois (pour initialiser le dossier .rockbox), puis reconnecter l'USB en 'Mode Stockage USB'.\n\nCliquez sur Continuer pour choisir un dossier sur votre lecteur.",
        "es": "Desconecta el USB, inicia Rockbox una vez (para que inicialice su carpeta .rockbox), luego reconecta el USB y selecciona 'Modo de almacenamiento USB'.\n\nHaz clic en Continuar para seleccionar cualquier carpeta en tu reproductor.",
    },
    "sel_themepack_select_folder": {
        "zh-CN": "选择已连接播放器磁盘上的任意文件夹",
        "en": "Select any folder on your connected player's storage",
        "fr": "Sélectionnez n'importe quel dossier sur le stockage de votre lecteur",
        "es": "Selecciona cualquier carpeta en el almacenamiento de tu reproductor",
    },
    "sel_themepack_success": {
        "zh-CN": "Rockbox 主题包已成功安装到您的播放器！",
        "en": "Rockbox Theme Pack successfully installed onto your device!",
        "fr": "Pack de thèmes Rockbox installé avec succès !",
        "es": "¡Paquete de temas Rockbox instalado con éxito en tu dispositivo!",
    },
    "sel_themepack_failed": {
        "zh-CN": "主题包安装失败: {err}",
        "en": "Theme Pack installation failed: {err}",
        "fr": "Échec de l'installation du pack de thèmes : {err}",
        "es": "Error al instalar el paquete de temas: {err}",
    },
    "sel_dialog_filter": {
        "zh-CN": "固件文件 (*.zip *.rar *scatter*.txt *.txt);;压缩包 (*.zip *.rar);;Scatter 分散加载文件 (*scatter*.txt *.txt);;所有文件 (*.*)",
        "en": "Firmware Files (*.zip *.rar *scatter*.txt *.txt);;Archives (*.zip *.rar);;Scatter Files (*scatter*.txt *.txt);;All Files (*.*)",
        "fr": "Fichiers de firmware (*.zip *.rar *scatter*.txt *.txt);;Archives (*.zip *.rar);;Fichiers Scatter (*scatter*.txt *.txt);;Tous les fichiers (*.*)",
        "es": "Archivos de firmware (*.zip *.rar *scatter*.txt *.txt);;Archivos (*.zip *.rar);;Archivos Scatter (*scatter*.txt *.txt);;Todos los archivos (*.*)",
    },
    "sel_only_local": {
        "zh-CN": "选择本地的 .zip 或 .rar 刷机包即可离线刷机——无需联网，支持所有 Innioasis 和 Timmkoo 音频播放器。",
        "en": "Pick a local .zip or .rar firmware package, or a folder / scatter .txt file, for offline installation — no internet needed. Works with every Innioasis and Timmkoo audio player.",
        "fr": "Choisissez un paquet firmware local .zip ou .rar pour une installation hors ligne — aucune connexion requise. Compatible avec tous les lecteurs audio Innioasis et Timmkoo.",
        "es": "Elige un paquete de firmware local .zip o .rar para instalar sin conexión: no hace falta Internet. Funciona con todos los reproductores de audio Innioasis y Timmkoo.",
    },
    "sel_only_local_generic": {
        "zh-CN": "选择本地固件包（.zip、.rar）或包含 scatter 文件的固件文件夹即可离线刷机——支持所有通用 MediaTek 设备。",
        "en": "Pick a local firmware package (.zip, .rar) or a folder containing a scatter file for offline installation — works with generic MediaTek devices.",
        "fr": "Choisissez un paquet de firmware local (.zip, .rar) ou un dossier contenant un fichier scatter pour une installation hors ligne — compatible avec les appareils MediaTek génériques.",
        "es": "Selecciona un paquete de firmware local (.zip, .rar) o una carpeta que contenga un archivo scatter para una instalación sin conexión — compatible con dispositivos MediaTek genéricos.",
    },
    "sel_offline_install": {
        "zh-CN": "可从 .zip 或 .rar 文件安装固件，适用于所有 Innioasis 和 Timmkoo 音频播放器。",
        "en": "Install firmware from a .zip or .rar file on any Innioasis or Timmkoo audio player.",
        "fr": "Installez un firmware à partir d'un fichier .zip ou .rar sur n'importe quel lecteur audio Innioasis ou Timmkoo.",
        "es": "Instala firmware desde un archivo .zip o .rar en cualquier reproductor de audio Innioasis o Timmkoo.",
    },
    "sel_offline_install_generic": {
        "zh-CN": "从 .zip、.rar 压缩包或固件文件夹安装固件，适用于通用 MediaTek 设备。",
        "en": "Install firmware from a .zip/.rar archive or firmware folder on generic MediaTek devices.",
        "fr": "Installez un firmware à partir d'une archive .zip/.rar ou d'un dossier sur des appareils MediaTek génériques.",
        "es": "Instala firmware desde un archivo .zip/.rar o carpeta en dispositivos MediaTek genéricos.",
    },
    "sel_btn_start": {
        "zh-CN": "安装 / 恢复",
        "en": "Install / Restore",
        "fr": "Installer / Restaurer",
        "es": "Instalar / Restaurar",
    },
    "installed_badge": {
        "zh-CN": "当前已安装",
        "en": "Installed",
        "fr": "Installé",
        "es": "Instalado",
    },
    "sel_installed_tip": {
        "zh-CN": "已安装的版本",
        "en": "Installed version",
        "fr": "Version installée",
        "es": "Versión instalada",
        "de": "Installierte Version",
        "ja": "インストール済みのバージョン",
    },
    "sel_update_available": {
        "zh-CN": "你的 {model} 有 {software} 的更新可用。",
        "en": "There's an update to {software} available for your {model}.",
        "fr": "Une mise à jour de {software} est disponible pour votre {model}.",
        "es": "Hay una actualización de {software} disponible para tu {model}.",
        "de": "Für dein {model} ist ein Update für {software} verfügbar.",
        "ja": "お使いの {model} 向けに {software} のアップデートがあります。",
    },
    "sel_updates_available": {
        "zh-CN": "有可用的更新。",
        "en": "Updates are available.",
        "fr": "Des mises à jour sont disponibles.",
        "es": "Hay actualizaciones disponibles.",
        "de": "Updates sind verfügbar.",
        "ja": "アップデートがあります。",
    },
    "toast_dismiss": {
        "zh-CN": "关闭",
        "en": "Dismiss",
        "fr": "Fermer",
        "es": "Cerrar",
        "de": "Schließen",
        "ja": "閉じる",
    },
    "toast_battery_charged": {
        "zh-CN": "在您的播放器上使用本工具之前，请确保电池已充满电。",
        "en": "Before using this on your player, charge the battery for at least an hour",
        "fr": "Avant d'utiliser cet outil sur votre lecteur, chargez la batterie pendant au moins une heure.",
        "es": "Antes de usar esto en tu reproductor, asegúrate de que la batería esté cargada.",
        "de": "Bevor Sie dieses Tool auf Ihrem Player verwenden, stellen Sie sicher, dass der Akku geladen ist.",
        "ja": "プレーヤーで使用する前に、バッテリーが充電されていることを確認してください。",
    },
    "toast_battery_charged_device": {
        "zh-CN": "在您的设备上使用本工具之前，请确保电池已充满电。",
        "en": "Before using this on your device, charge the battery for at least an hour",
        "fr": "Avant d'utiliser cet outil sur votre appareil, chargez la batterie pendant au moins une heure.",
        "es": "Antes de usar esto en tu dispositivo, asegúrate de que la batería esté cargada.",
        "de": "Bevor Sie dieses Tool auf Ihrem Gerät verwenden, stellen Sie sicher, dass der Akku geladen ist.",
        "ja": "デバイスで使用する前に、バッテリーが充電されていることを確認してください。",
    },
    "toast_install": {
        "zh-CN": "安装",
        "en": "Install",
        "fr": "Installer",
        "es": "Instalar",
        "de": "Installieren",
        "ja": "インストール",
    },
    "toast_later": {
        "zh-CN": "稍后",
        "en": "Later",
        "fr": "Plus tard",
        "es": "Más tarde",
        "de": "Später",
        "ja": "後で",
    },
    "toast_dont_remind": {
        "zh-CN": "不再提醒",
        "en": "Do not remind me",
        "fr": "Ne plus me le rappeler",
        "es": "No recordarme",
        "de": "Nicht mehr erinnern",
        "ja": "このリリースは通知しない",
    },
    "sel_download_start": {
        "zh-CN": "开始下载…",
        "en": "Starting download…",
        "fr": "Démarrage du téléchargement…",
        "es": "Iniciando la descarga…",
    },
    "sel_download_done": {
        "zh-CN": "下载完成",
        "en": "Download complete",
        "fr": "Téléchargement terminé",
        "es": "Descarga completada",
    },
    "sel_download_failed": {
        "zh-CN": "下载失败",
        "en": "Download failed",
        "fr": "Échec du téléchargement",
        "es": "Error en la descarga",
    },
    "sel_preparing": {
        "zh-CN": "正在准备软件包…",
        "en": "Preparing package…",
        "fr": "Préparation du paquet…",
        "es": "Preparando el paquete…",
    },
    "sel_prepare_done": {
        "zh-CN": "软件包已就绪，可以开始刷机",
        "en": "Package ready — start the install",
        "fr": "Paquet prêt — démarrez l'installation",
        "es": "Paquete listo: inicia la instalación",
    },
    "sel_prepare_failed": {
        "zh-CN": "软件包准备失败",
        "en": "Could not prepare package",
        "fr": "Impossible de préparer le paquet",
        "es": "No se pudo preparar el paquete",
    },
    "sel_notes_hint": {
        "zh-CN": "选择左侧版本以查看更新日志",
        "en": "Select a release to view its changelog",
        "fr": "Sélectionnez une version pour afficher le journal des modifications",
        "es": "Selecciona una versión para ver su registro de cambios",
    },
    "sel_release_assets": {
        "zh-CN": "文件",
        "en": "Files",
        "fr": "Fichiers",
        "es": "Archivos",
    },

    # Flash page
    "flash_wait_title": {
        "zh-CN": "等待设备接入",
        "en": "Waiting for Device",
        "fr": "En attente de l'appareil",
        "es": "Esperando el dispositivo",
    },
    "flash_wait_desc": {
        "zh-CN": "请先关机，再连接机器；若已连接仍未识别，请更换 USB 线或接口后重试。",
        "en": "Power off the device first. If it is connected but not detected, try a different USB cable or port.",
        "fr": "Éteignez d'abord l'appareil. S'il est connecté mais non détecté, essayez un autre câble ou port USB.",
        "es": "Apaga primero el dispositivo. Si está conectado pero no se detecta, prueba con otro cable o puerto USB.",
    },
    "flash_download_in_progress": {
        "zh-CN": "正在下载",
        "en": "Download in Progress",
        "fr": "Téléchargement en cours",
        "es": "Descarga en curso",
    },
    "flash_install_in_progress": {
        "zh-CN": "正在安装",
        "en": "Install in Progress",
        "fr": "Installation en cours",
        "es": "Instalación en curso",
    },
    "flash_install_complete": {
        "zh-CN": "安装完成",
        "en": "Install complete",
        "fr": "Installation terminée",
        "es": "Instalación completada",
        "de": "Installation abgeschlossen",
        "ja": "インストール完了",
    },
    "dialog_pre_install_title": {
        "zh-CN": "准备设备",
        "en": "Device Preparation",
        "fr": "Préparation de l'appareil",
        "es": "Preparación del dispositivo",
    },
    "dialog_pre_install_header": {
        "zh-CN": "请确保您的 {model} 已关机且未连接 USB 数据线",
        "en": "Please make sure your {model} is powered off and not connected by USB",
        "fr": "Veuillez vous assurer que votre {model} est éteint et non connecté par USB",
        "es": "Asegúrate de que tu {model} esté apagado y no conectado por USB",
    },
    "dialog_pre_install_innioasis_tip": {
        "zh-CN": "• 刷机前请确保设备已完全关机。\n• 若设备卡死或屏幕无响应，请使用卡针或回形针按压复位孔强制关机以进入刷机模式。\n• 暂时保持 USB 数据线未连接。\n• 点击“继续”，稍后根据软件提示连接 USB 数据线。",
        "en": "• Completely power off your device before starting.\n• If the device is frozen or does not respond, use a pin or paperclip in the reset hole to force power off into download mode.\n• Keep the USB cable disconnected for now.\n• Click Continue, then connect your device via USB when prompted.",
        "fr": "• Éteignez complètement votre appareil avant de commencer.\n• Si l'appareil est figé ou ne répond pas, utilisez un trombone dans le trou de réinitialisation pour forcer l'extinction en mode téléchargement.\n• Laissez le câble USB déconnecté pour l'instant.\n• Cliquez sur Continuer, puis connectez votre appareil en USB lorsque vous y êtes invité.",
        "es": "• Apaga completamente tu dispositivo antes de comenzar.\n• Si el dispositivo está bloqueado o no responde, usa un clip o alfiler en el orificio de reinicio para apagarlo y entrar en modo de descarga.\n• Mantén el cable USB desconectado por ahora.\n• Haz clic en Continuar y conecta tu dispositivo por USB cuando se te indique.",
    },
    "dialog_pre_install_generic_tip": {
        "zh-CN": "• 请将您的 MediaTek 设备完全关机。\n• 暂时保持 USB 数据线未连接。\n• 根据具体机型，可尝试装入或拔出电池后连接（或在连接时按住音量加/减键）。\n• 点击“继续”，稍后根据提示连接 USB 数据线。",
        "en": "• Completely power off your MediaTek device.\n• Keep the USB cable disconnected for now.\n• Depending on the model, try connecting with or without battery inserted (or hold Volume Down / Up while connecting).\n• Click Continue, then connect the USB cable when prompted.",
        "fr": "• Éteignez complètement votre appareil MediaTek.\n• Laissez le câble USB déconnecté pour l'instant.\n• Selon le modèle, essayez de connecter avec ou sans batterie (ou en maintenant Volume -/+).\n• Cliquez sur Continuer, puis connectez le câble USB lorsque vous y êtes invité.",
        "es": "• Apaga completamente tu dispositivo MediaTek.\n• Mantén el cable USB desconectado por ahora.\n• Según el modelo, intenta conectar con o sin batería (o manteniendo presionado Volumen -/+).\n• Haz clic en Continuar y conecta el cable USB cuando se te indique.",
    },
    "btn_cancel": {
        "zh-CN": "取消",
        "en": "Cancel",
        "fr": "Annuler",
        "es": "Cancelar",
        "de": "Abbrechen",
        "ja": "キャンセル",
    },
    "btn_next": {
        "zh-CN": "下一步",
        "en": "Next",
        "fr": "Suivant",
        "es": "Siguiente",
        "de": "Weiter",
        "ja": "次へ",
    },
    "dialog_pre_install_continue": {
        "zh-CN": "继续",
        "en": "Continue",
        "fr": "Continuer",
        "es": "Continuar",
    },
    "flash_connect_device_prompt": {
        "zh-CN": "请连接您的 {model}",
        "en": "Please connect your {model}",
        "fr": "Veuillez connecter votre {model}",
        "es": "Por favor conecta tu {model}",
    },
    "flash_please_wait": {
        "zh-CN": "请稍候",
        "en": "Please wait",
        "fr": "Veuillez patienter",
        "es": "Espera un momento",
        "de": "Bitte warten",
        "ja": "しばらくお待ちください",
    },
    "flash_wait_connecting_prompt": {
        "zh-CN": "请稍候，正在连接您的 {model}",
        "en": "Please wait while {model} connects",
        "fr": "Veuillez patienter pendant la connexion de votre {model}",
        "es": "Espera un momento mientras se conecta tu {model}",
        "de": "Bitte warten, während Ihr {model} verbunden wird",
        "ja": "{model} の接続を待っています",
    },
    "device_fallback_word": {
        "zh-CN": "设备",
        "en": "device",
        "fr": "appareil",
        "es": "dispositivo",
    },
    "flash_connect_prompt": {
        "zh-CN": "请确保您的 {model} 已关机并断开连接，然后点击下一步",
        "en": "Please make sure your {model} is powered-off and disconnected - then press Next.",
        "fr": "Veuillez vous assurer que votre {model} est éteint et déconnecté, puis appuyez sur Suivant",
        "es": "Por favor, asegúrate de que tu {model} esté apagado y desconectado, luego pulsa Siguiente",
        "de": "Bitte stellen Sie sicher, dass Ihr {model} ausgeschaltet und getrennt ist – dann drücken Sie auf Weiter",
        "ja": "{model}の電源を切り、取り外されていることを確認してから、「次へ」を押してください",
    },
    "flash_connect_prompt_power": {
        "zh-CN": "请确保您的 {model} 已关机并断开连接，然后点击下一步",
        "en": "Please make sure your {model} is powered-off and disconnected - then press Next.",
        "fr": "Veuillez vous assurer que votre {model} est éteint et déconnecté, puis appuyez sur Suivant",
        "es": "Por favor, asegúrate de que tu {model} esté apagado y desconectado, luego pulsa Siguiente",
        "de": "Bitte stellen Sie sicher, dass Ihr {model} ausgeschaltet und getrennt ist – dann drücken Sie auf Weiter",
        "ja": "{model}の電源を切り、取り外されていることを確認してから、「次へ」を押してください",
    },
    "flash_connect_prompt_mtk": {
        "zh-CN": "请确保您的 {model} 已通过 USB 连接，然后点击下一步",
        "en": "Please make sure your {model} is connected by USB - then press Next.",
        "fr": "Veuillez vous assurer que votre {model} est connecté par USB, puis appuyez sur Suivant",
        "es": "Por favor, asegúrate de que tu {model} esté conectado por USB, luego pulsa Siguiente",
        "de": "Bitte stellen Sie sicher, dass Ihr {model} über USB verbunden ist – dann drücken Sie auf Weiter",
        "ja": "{model}がUSBで接続されていることを確認してから、「次へ」を押してください",
    },
    "flash_connect_paperclip_prompt": {
        "zh-CN": "请用卡针/回形针按下耳机插孔旁边的按钮（若未开始请再次按下，直到安装进度开始）。",
        "en": "Tip: Power off the device by pressing the hidden button beside the headphone jack with a paperclip or pin.",
        "fr": "Astuce : éteignez l'appareil en appuyant avec un trombone ou une aiguille sur le bouton caché à côté de la prise casque.",
        "es": "Consejo: apague el dispositivo pulsando con un clip o una aguja el botón oculto junto a la toma de auriculares.",
    },
    "flash_connect_paperclip_prompt_a5": {
        "zh-CN": "请用卡针/回形针按下电源键附近的隐藏按钮（若未开始请再次按下，直到安装进度开始）。",
        "en": "Tip: Power off your A5 by pressing the hidden button beside the power button with a clip using a paperclip or pin..",
        "fr": "Astuce : éteignez votre A5 en appuyant avec un trombone ou une aiguille sur le bouton caché à côté du bouton d'alimentation en utilisant un trombone ou un épingle.",
        "es": "Consejo: apague su A5 pulsando con un clip o una aguja el botón oculto junto al botón de encendido usando un clip o una aguja..",
    },
    "flash_paperclip_hint": {
        "zh-CN": "用卡针或回形针按下耳机接口和 USB 接口之间的隐藏按钮以完全关闭您的 {model}。",
        "en": "Completely power off your {model} by pressing the hidden button between the headphone and USB ports using a paperclip or pin.",
        "fr": "Éteignez complètement votre {model} en appuyant sur le bouton caché entre la prise casque et le port USB à l'aide d'un trombone ou d'une aiguille.",
        "es": "Apaga completamente tu {model} presionando el botón oculto entre el puerto de auriculares y el puerto USB con un clip o alfiler.",
        "de": "Schalten Sie Ihr {model} vollständig aus, indem Sie die versteckte Taste zwischen Kopfhörer- und USB-Anschluss mit einer Büroklammer oder Nadel drücken.",
        "ja": "クリップまたはピンを使用して、イヤホン端子とUSBポートの間にある隠しボタンを押して、{model}の電源を完全に切ってください。",
    },
    "flash_paperclip_hint_a5": {
        "zh-CN": "用卡针或回形针按下电源键旁边的隐藏按钮以完全关闭您的 A5。",
        "en": "Completely power off your A5 by pressing the hidden button beside the power button with a clip using a paperclip or pin.",
        "fr": "Éteignez complètement votre A5 en appuyant sur le bouton caché à côté du bouton d'alimentation à l'aide d'un trombone ou d'une aiguille.",
        "es": "Apaga completamente tu A5 presionando el botón oculto al lado del botón de encendido con un clip o alfiler.",
        "de": "Schalten Sie Ihr A5 vollständig aus, indem Sie die versteckte Taste neben dem Ein-/Ausschalter mit einer Büroklammer oder Nadel drücken.",
        "ja": "クリップまたはピンを使用して、電源ボタンの横にある隠しボタンを押して、A5の電源を完全に切ってください。",
    },
    "flash_y1_preloader_hint": {
        "zh-CN": "提示：用回形针或卡针按下耳机插孔旁边的隐藏按钮关机，关机后直接连接 USB 即可（无需长按按键）。",
        "en": "Tip: Power off by pressing the hidden button beside the headphone jack with a paperclip or pin. Connect USB normally without holding buttons.",
        "fr": "Astuce : éteignez en appuyant sur le bouton caché à côté de la prise casque avec un trombone ou une aiguille, puis connectez l'USB normalement.",
        "es": "Consejo: apague pulsando el botón oculto junto a la toma de auriculares con un clip o una aguja, luego conecte el USB normalmente.",
    },
    "gui_handoff_hint_headphone": {
        "zh-CN": "用卡针或回形针插入耳机孔旁边的小孔来关机，然后在桌面工具等待时连接 USB。",
        "en": "Power off by placing a pin or paperclip in the hole next to the headphone port, then connect USB when the desktop tool is waiting.",
        "fr": "Éteignez l'appareil en plaçant une épingle ou un trombone dans le trou à côté de la prise casque, puis branchez l'USB lorsque l'outil de bureau est en attente.",
        "es": "Apágalo introduciendo un alfiler o un clip en el orificio junto al puerto de auriculares y conecta el USB cuando la herramienta de escritorio esté esperando.",
        "de": "Schalte aus, indem du eine Nadel oder Büroklammer in das Loch neben der Kopfhörerbuchse steckst, und schließe USB an, wenn das Desktop-Programm wartet.",
        "ja": "イヤホン端子の横の穴にピンまたはクリップを差し込んで電源を切り、デスクトップツールが待機しているときに USB を接続してください。",
    },
    "gui_handoff_hint_a5": {
        "zh-CN": "将卡针或回形针插入电源键下方的小孔，然后在桌面工具等待时连接 USB。",
        "en": "Insert the pin or paperclip in the hole beneath the power button, then connect USB when the desktop tool is waiting.",
        "fr": "Insérez l'épingle ou le trombone dans le trou sous le bouton d'alimentation, puis branchez l'USB lorsque l'outil de bureau est en attente.",
        "es": "Introduce el alfiler o el clip en el orificio bajo el botón de encendido y conecta el USB cuando la herramienta de escritorio esté esperando.",
        "de": "Stecke die Nadel oder Büroklammer in das Loch unter der Ein-/Aus-Taste und schließe USB an, wenn das Desktop-Programm wartet.",
        "ja": "電源ボタンの下の穴にピンまたはクリップを差し込み、デスクトップツールが待機しているときに USB を接続してください。",
    },
    "gui_handoff_hint_generic": {
        "zh-CN": "请关闭播放器，断开 USB，并在工具要求时再连接。",
        "en": "Power the player off, disconnect USB, and connect it when the tool asks.",
        "fr": "Éteignez le lecteur, débranchez l'USB et rebranchez-le lorsque l'outil le demande.",
        "es": "Apaga el reproductor, desconecta el USB y conéctalo cuando la herramienta lo pida.",
        "de": "Schalte den Player aus, trenne USB und schließe es an, wenn das Programm danach fragt.",
        "ja": "プレーヤーの電源を切り、USB を外し、ツールが求めたときに接続してください。",
    },
    "gui_handoff_hint_generic_mediatek": {
        "zh-CN": "请关闭设备并断开 USB，在工具要求写入固件时再连接。",
        "en": "Power the device off, disconnect USB, and connect it when the tool asks for the firmware.",
        "fr": "Éteignez l'appareil, débranchez l'USB et rebranchez-le lorsque l'outil demande le micrologiciel.",
        "es": "Apaga el dispositivo, desconecta el USB y conéctalo cuando la herramienta pida el firmware.",
        "de": "Schalte das Gerät aus, trenne USB und schließe es an, wenn das Programm nach der Firmware fragt.",
        "ja": "デバイスの電源を切り、USB を外し、ツールがファームウェアを求めるときに接続してください。",
    },
    "gui_handoff_failed_title": {
        "zh-CN": "无法打开工具",
        "en": "Could not open the tool",
        "fr": "Impossible d'ouvrir l'outil",
        "es": "No se pudo abrir la herramienta",
        "de": "Programm konnte nicht geöffnet werden",
        "ja": "ツールを開けませんでした",
    },
    "gui_handoff_failed": {
        "zh-CN": "无法打开桌面工具。",
        "en": "The desktop tool could not be opened.",
        "fr": "Impossible d'ouvrir l'outil de bureau.",
        "es": "No se pudo abrir la herramienta de escritorio.",
        "de": "Das Desktop-Programm konnte nicht geöffnet werden.",
        "ja": "デスクトップツールを開けませんでした。",
    },
    "gui_handoff_no_package": {
        "zh-CN": "请先选择一个软件版本。",
        "en": "Choose a software release first.",
        "fr": "Choisissez d'abord une version du logiciel.",
        "es": "Elige primero una versión de software.",
        "de": "Wähle zuerst eine Softwareversion.",
        "ja": "先にソフトウェアリリースを選んでください。",
    },
    "gui_handoff_no_package_mediatek": {
        "zh-CN": "请先选择一个固件包。",
        "en": "Choose a firmware package first.",
        "fr": "Choisissez d'abord un paquet de micrologiciel.",
        "es": "Elige primero un paquete de firmware.",
        "de": "Wähle zuerst ein Firmware-Paket.",
        "ja": "先にファームウェアを選んでください。",
    },
    # Settings: run installs with superuser rights. The platform's own
    # elevation question, asked once for the app so every install method it
    # spawns (SP Flash Tool console, SP Flash Tool GUI, MTKClient, terminal)
    # runs with the same rights.
    "settings_run_as_admin": {
        "zh-CN": "以管理员身份运行",
        "en": "Run as administrator",
        "fr": "Exécuter en tant qu'administrateur",
        "es": "Ejecutar como administrador",
    },
    "settings_run_as_admin_desc": {
        "zh-CN": "开始安装时向 Windows 请求管理员权限。SP Flash Tool 与 MTKClient 将以完整权限运行——当驱动或端口无法打开时的排查途径。",
        "en": "Ask Windows for administrator rights when an install starts. SP Flash Tool and MTKClient then run with full access — a troubleshooting route when a driver or port refuses to open.",
        "fr": "Demander les droits d'administrateur à Windows au début d'une installation. SP Flash Tool et MTKClient disposent alors d'un accès complet — une piste de dépannage quand un pilote ou un port refuse de s'ouvrir.",
        "es": "Pedir permisos de administrador a Windows al iniciar una instalación. SP Flash Tool y MTKClient tendrán acceso completo: una vía de diagnóstico cuando un controlador o un puerto no se abre.",
    },
    "settings_run_as_superuser": {
        "zh-CN": "以超级用户身份运行",
        "en": "Run as superuser",
        "fr": "Exécuter en tant que superutilisateur",
        "es": "Ejecutar como superusuario",
    },
    "settings_run_as_superuser_desc": {
        "zh-CN": "通过 pkexec 以 root 权限运行安装。当权限错误阻止刷机工具时使用。",
        "en": "Run installs with root rights through pkexec. Use it when a permission error blocks the flash tool.",
        "fr": "Exécuter les installations avec les droits root via pkexec. À utiliser lorsqu'une erreur de permission bloque l'outil de flash.",
        "es": "Ejecutar las instalaciones con permisos de root mediante pkexec. Úsalo cuando un error de permisos bloquee la herramienta de flasheo.",
    },
    "settings_run_as_admin_restart_needed": {
        "zh-CN": "更新程序 需要重启，安装才能获得这些权限。",
        "en": "Updater CE must restart for installs to run with these rights.",
        "fr": "Programme de mise à jour doit redémarrer pour que les installations disposent de ces droits.",
        "es": "Actualizador debe reiniciarse para que las instalaciones tengan estos permisos.",
    },
    "settings_run_as_admin_restart": {
        "zh-CN": "以管理员身份重启",
        "en": "Restart as administrator",
        "fr": "Redémarrer en tant qu'administrateur",
        "es": "Reiniciar como administrador",
    },
    "settings_run_as_admin_unavailable": {
        "zh-CN": "此桌面没有可用的提权工具。",
        "en": "This desktop has no elevation helper available.",
        "fr": "Ce bureau n'a aucun outil d'élévation disponible.",
        "es": "Este escritorio no tiene ninguna herramienta de elevación disponible.",
    },
    "flash_preparing": {
        "zh-CN": "正在准备固件包… 请暂时保持设备未连接。",
        "en": "Preparing firmware package… Keep the device unplugged for now.",
        "fr": "Préparation du paquet firmware… Laissez l'appareil débranché pour le moment.",
        "es": "Preparando el paquete de firmware… Mantén el dispositivo desenchufado por ahora.",
    },
    "flash_searching": {
        "zh-CN": "正在搜索您的设备… 现在请关机，并连接 USB 线。",
        "en": "Searching for your device… Now power off the device and connect the USB cable.",
        "fr": "Recherche de votre appareil… Éteignez maintenant l'appareil et connectez le câble USB.",
        "es": "Buscando tu dispositivo… Ahora apaga el dispositivo y conecta el cable USB.",
    },
    "flash_guide_title": {
        "zh-CN": "连接引导",
        "en": "Connection Guide",
        "fr": "Guide de connexion",
        "es": "Guía de conexión",
    },
    "flash_guide_1": {
        "zh-CN": "1. 先关闭机器电源（如有需要可使用卡针或回形针戳复位孔）",
        "en": "1. Power off the device (use a pin or paperclip in reset hole if needed)",
        "fr": "1. Éteignez l'appareil (utilisez un trombone/épingle dans le trou de réinitialisation si nécessaire)",
        "es": "1. Apaga el dispositivo (usa un clip o alfiler en el orificio de reinicio si es necesario)",
    },
    "flash_guide_2": {
        "zh-CN": "使用 USB 线连接电脑与机器",
        "en": "2. Connect via USB cable",
        "fr": "2. Connectez via un câble USB",
        "es": "2. Conéctalo con un cable USB",
    },
    "flash_guide_3": {
        "zh-CN": "等待软件识别设备",
        "en": "3. Wait for device detection",
        "fr": "3. Attendez la détection",
        "es": "3. Espera la detección",
    },
    "flash_guide_4": {
        "zh-CN": "识别到设备后自动开始刷机",
        "en": "4. Flashing starts automatically after detection",
        "fr": "4. Le flashing démarre automatiquement après la détection",
        "es": "4. El flashing comienza automáticamente tras la detección",
    },
    "flash_warning": {
        "zh-CN": "请勿断电、不要拔出 USB 线、不要关闭刷机工具。",
        "en": "Do not unplug power, USB cable, or close the tool.",
        "fr": "Ne débranchez pas l'alimentation, ne retirez pas le câble USB et ne fermez pas l'outil.",
        "es": "No desconectes la alimentación, no retires el cable USB ni cierres la herramienta.",
    },
    "flash_btn_cancel": {
        "zh-CN": "取消",
        "en": "Cancel",
        "fr": "Annuler",
        "es": "Cancelar",
    },
    "flash_btn_cancel_wait": {
        "zh-CN": "返回软件选择",
        "en": "Back to Select Software",
        "fr": "Retour à la sélection logicielle",
        "es": "Volver a seleccionar software",
    },
    "flash_btn_quit": {
        "zh-CN": "退出",
        "en": "Quit",
        "fr": "Quitter",
        "es": "Salir",
    },
    "flash_btn_retry": {
        "zh-CN": "重试",
        "en": "Retry",
        "fr": "Réessayer",
        "es": "Reintentar",
        "de": "Erneut versuchen",
        "ja": "再試行",
    },
    "flash_btn_retry_detect": {
        "zh-CN": "重新检测设备",
        "en": "Re-detect Device",
        "fr": "Redétecter l'appareil",
        "es": "Volver a detectar dispositivo",
    },
    "flash_btn_view_log": {
        "zh-CN": "查看诊断",
        "en": "View Diagnostics",
        "fr": "Voir les diagnostics",
        "es": "Ver diagnóstico",
    },
    "flash_status_panel": {
        "zh-CN": "状态面板",
        "en": "Status Panel",
        "fr": "Panneau d'état",
        "es": "Panel de estado",
    },
    "flash_progress_title": {
        "zh-CN": "升级进度",
        "en": "Upgrade Progress",
        "fr": "Progression de la mise à niveau",
        "es": "Progreso de la actualización",
    },
    "flash_conn_status": {
        "zh-CN": "连接状态",
        "en": "Connection Status",
        "fr": "État de la connexion",
        "es": "Estado de la conexión",
    },
    "flash_device_status": {
        "zh-CN": "设备状态",
        "en": "Device Status",
        "fr": "État de l'appareil",
        "es": "Estado del dispositivo",
    },
    "flash_current_step": {
        "zh-CN": "当前步骤",
        "en": "Current Step",
        "fr": "Étape actuelle",
        "es": "Paso actual",
    },
    "flash_elapsed": {
        "zh-CN": "已用时间",
        "en": "Elapsed",
        "fr": "Écoulé",
        "es": "Transcurrido",
    },
    "flash_eta": {
        "zh-CN": "预计剩余",
        "en": "ETA",
        "fr": "Estimation",
        "es": "Estimación",
    },
    "flash_eta_second_one": {
        "zh-CN": "剩余约 1 秒",
        "en": "About 1 second remaining",
        "fr": "Environ 1 seconde restante",
        "es": "Aproximadamente 1 segundo restante",
    },
    "flash_eta_seconds_many": {
        "zh-CN": "剩余约 {n} 秒",
        "en": "About {n} seconds remaining",
        "fr": "Environ {n} secondes restantes",
        "es": "Aproximadamente {n} segundos restantes",
    },
    "flash_eta_minute_one": {
        "zh-CN": "剩余约 1 分钟",
        "en": "About 1 minute remaining",
        "fr": "Environ 1 minute restante",
        "es": "Aproximadamente 1 minuto restante",
    },
    "flash_eta_minutes_many": {
        "zh-CN": "剩余约 {n} 分钟",
        "en": "About {n} minutes remaining",
        "fr": "Environ {n} minutes restantes",
        "es": "Aproximadamente {n} minutos restantes",
    },
    "flash_eta_remaining_fallback": {
        "zh-CN": "剩余约 {eta}",
        "en": "About {eta} remaining",
        "fr": "Environ {eta} restant",
        "es": "Aproximadamente {eta} restante",
    },
    "flash_current_pkg": {
        "zh-CN": "当前软件包",
        "en": "Current Package",
        "fr": "Paquet actuel",
        "es": "Paquete actual",
    },
    "flash_current_action": {
        "zh-CN": "当前动作：{step}",
        "en": "Current action: {step}",
        "fr": "Action actuelle : {step}",
        "es": "Acción actual: {step}",
    },
    "flash_waiting_device": {
        "zh-CN": "等待设备接入…",
        "en": "Waiting for device…",
        "fr": "En attente de l'appareil…",
        "es": "Esperando el dispositivo…",
    },
    "flash_model_generic": {
        "zh-CN": "设备",
        "en": "device",
        "fr": "appareil",
        "es": "dispositivo",
    },
    "device_generic": {
        "zh-CN": "设备",
        "en": "device",
        "fr": "appareil",
        "es": "dispositivo",
    },
    "flash_method": {
        "zh-CN": "刷机方式",
        "en": "Flash Method",
        "fr": "Méthode de flashing",
        "es": "Método de flashing",
    },
    "flash_method_auto": {
        "zh-CN": "自动（推荐）",
        "en": "Auto (recommended)",
        "fr": "Auto (recommandé)",
        "es": "Auto (recomendado)",
    },
    "flash_method_sp": {
        "zh-CN": "SP Flash Tool",
        "en": "SP Flash Tool",
        "fr": "SP Flash Tool",
        "es": "SP Flash Tool",
    },
    "flash_method_mtk": {
        "zh-CN": "MTKClient",
        "en": "MTKClient",
        "fr": "MTKClient",
        "es": "MTKClient",
    },
    "flash_method_note_auto": {
        "zh-CN": "自动：Windows 与 Linux 默认使用 SP Flash Tool，macOS 使用 MTKClient。所有平台均可手动切换。",
        "en": "Auto: SP Flash Tool on Windows and Linux, MTKClient on macOS. You can switch manually on any platform.",
        "fr": "Auto : SP Flash Tool sous Windows et Linux, MTKClient sous macOS. Vous pouvez basculer manuellement sur n'importe quelle plateforme.",
        "es": "Auto: SP Flash Tool en Windows y Linux, MTKClient en macOS. Puede cambiar manualmente en cualquier plataforma.",
    },
    "flash_method_note_sp": {
        "zh-CN": "SP Flash Tool——联发科官方工具；在 Linux 上首次使用时自动下载并部署社区版。",
        "en": "SP Flash Tool — MediaTek's official flasher. On Linux the community build is downloaded and staged on first use.",
        "fr": "SP Flash Tool — l'outil officiel de MediaTek. Sous Linux, la version communautaire est téléchargée et installée lors de la première utilisation.",
        "es": "SP Flash Tool, la herramienta oficial de MediaTek. En Linux, la versión comunitaria se descarga e instala en el primer uso.",
    },
    "flash_btn_open_sp_gui": {
        "zh-CN": "打开 SP Flash Tool 界面",
        "en": "Open SP Flash Tool GUI",
        "fr": "Ouvrir SP Flash Tool GUI",
        "es": "Abrir SP Flash Tool GUI",
    },
    "sp_gui_title": {
        "zh-CN": "SP Flash Tool 界面",
        "en": "SP Flash Tool GUI",
        "fr": "SP Flash Tool GUI",
        "es": "SP Flash Tool GUI",
    },
    "sp_gui_launch_intro": {
        "zh-CN": "SP Flash Tool 图形界面即将启动。\n\n• 如果设备尚未关机，请将其关机。\n• 如果已连接 USB 线，请先断开连接。\n• 在 SP Flash Tool 中根据需要点击 Download（或 Format All + Download）。\n\n准备就绪后连接 USB 线即可开始刷机。",
        "en": "SP Flash Tool GUI will now launch.\n\n• If it isn't already off, power off your device.\n• If it is connected to USB, disconnect it first.\n• In SP Flash Tool, click Download (or Format All + Download as needed).\n\nThen connect the USB cable to begin flashing.",
        "fr": "L'interface graphique de SP Flash Tool va maintenant démarrer.\n\n• Si votre appareil n'est pas déjà éteint, éteignez-le.\n• S'il est connecté en USB, débranchez-le d'abord.\n• Dans SP Flash Tool, cliquez sur Download (ou Format All + Download).\n\nConnectez ensuite le câble USB pour commencer.",
        "es": "La interfaz gráfica de SP Flash Tool se iniciará ahora.\n\n• Si aún no está apagado, apaga tu dispositivo.\n• Si está conectado por USB, desconéctalo primero.\n• En SP Flash Tool, haz clic en Download (o Format All + Download según sea necesario).\n\nLuego conecta el cable USB para comenzar a flashear.",
    },
    "sp_gui_launched_status": {
        "zh-CN": "SP Flash Tool 界面已启动",
        "en": "SP Flash Tool GUI launched",
        "fr": "SP Flash Tool GUI lancé",
        "es": "SP Flash Tool GUI iniciado",
    },
    "sp_gui_not_supported": {
        "zh-CN": "SP Flash Tool 图形界面仅在 Windows 和支持的 Linux (x86/x86_64) 系统上可用。",
        "en": "SP Flash Tool GUI is only available on Windows and supported Linux (x86/x86_64) systems.",
        "fr": "L'interface graphique SP Flash Tool est uniquement disponible sous Windows et Linux (x86/x86_64).",
        "es": "La interfaz gráfica de SP Flash Tool solo está disponible en Windows y sistemas Linux (x86/x86_64) compatibles.",
    },
    "sp_gui_error_title": {
        "zh-CN": "无法启动 SP Flash Tool",
        "en": "Could Not Launch SP Flash Tool",
        "fr": "Impossible de lancer SP Flash Tool",
        "es": "No se pudo iniciar SP Flash Tool",
    },
    "sp_gui_error_desc": {
        "zh-CN": "启动 SP Flash Tool 时出错：",
        "en": "Failed to launch SP Flash Tool GUI:",
        "fr": "Échec du lancement de l'interface graphique SP Flash Tool :",
        "es": "No se pudo iniciar la interfaz gráfica de SP Flash Tool:",
    },
    "sp_gui_preparing_firmware": {
        "en": "The firmware package is incomplete — downloading and extracting it before opening SP Flash Tool.",
        "zh-CN": "固件包不完整——正在下载并解压，然后打开 SP Flash Tool。",
        "fr": "Le paquet de micrologiciel est incomplet — téléchargement et extraction avant d'ouvrir SP Flash Tool.",
        "es": "El paquete de firmware está incompleto: se descargará y extraerá antes de abrir SP Flash Tool.",
    },
    "install_needs_admin_title": {
        "en": "One quick permission check",
        "zh-CN": "需要一次权限确认",
        "fr": "Une autorisation rapide",
        "es": "Una autorización rápida",
    },
    "install_needs_admin_body": {
        "en": "Before the update starts, Linux may ask for your password so we can set up your computer to talk to your player. This helps the connection work reliably. You can choose Continue without a password instead; we’ll still try the update with your current access.",
        "zh-CN": "开始前，Linux 可能会询问一次密码，以便为播放器准备安全可靠的连接。如果不想输入密码，也可以选择不输入密码继续；更新仍会使用当前权限尝试进行。",
        "fr": "Avant de commencer, Linux peut demander votre mot de passe une fois pour préparer une connexion fiable et sécurisée à votre lecteur. Vous préférez ne pas le saisir ? Choisissez de continuer sans mot de passe ; la mise à jour tentera quand même de démarrer avec vos droits actuels.",
        "es": "Antes de empezar, Linux puede pedirte la contraseña una vez para preparar una conexión segura y fiable con el reproductor. ¿Prefieres no introducirla? Elige continuar sin contraseña; la actualización intentará continuar con tus permisos actuales.",
    },
    "install_continue_without_password": {
        "en": "Continue without a password",
        "zh-CN": "不输入密码继续",
        "fr": "Continuer sans mot de passe",
        "es": "Continuar sin contraseña",
    },
    "install_prepare_computer": {
        "en": "Prepare computer",
        "zh-CN": "准备电脑",
        "fr": "Préparer l'ordinateur",
        "es": "Preparar el equipo",
    },
    "install_without_admin": {
        "en": "Continuing with your current computer permissions.",
        "zh-CN": "将在没有管理员权限的情况下继续。",
        "fr": "Poursuite sans droits d'administrateur.",
        "es": "Se continúa sin permisos de administrador.",
    },
    "install_linux_permission_prompt": {
        "en": "Your computer needs one-time permission to talk to your player. Click Next, then enter your password when asked.",
        "zh-CN": "电脑需要一次性授权才能与播放器通信。请点击“下一步”，然后在提示时输入密码。",
        "fr": "Votre ordinateur a besoin d'une autorisation unique pour communiquer avec le lecteur. Cliquez sur Suivant, puis saisissez votre mot de passe.",
        "es": "El equipo necesita un permiso único para comunicarse con el reproductor. Haga clic en Siguiente e introduzca su contraseña cuando se le pida.",
    },
    "install_linux_entering_password": {
        "en": "Please enter your password in the prompt to allow access to your device…",
        "zh-CN": "请在提示框中输入密码以允许访问您的设备…",
        "fr": "Veuillez saisir votre mot de passe dans l'invite pour autoriser l'accès à votre appareil…",
        "es": "Por favor, introduzca su contraseña para permitir el acceso a su dispositivo…",
        "de": "Bitte geben Sie Ihr Passwort ein, um den Zugriff auf Ihr Gerät zu erlauben…",
        "ja": "デバイスへのアクセスを許可するためにプロンプトにパスワードを入力してください…",
    },
    "install_linux_prep_pending": {
        "en": "Getting your computer ready to connect to your player…",
        "zh-CN": "正在准备电脑以可靠连接播放器…",
        "fr": "Préparation de l'ordinateur pour une connexion fiable au lecteur…",
        "es": "Preparando el equipo para una conexión fiable con el reproductor…",
    },
    "install_linux_prep_pending_friendly": {
        "en": "Preparing your computer for the software update…",
        "zh-CN": "正在为软件更新准备电脑…",
        "fr": "Préparation de votre ordinateur pour la mise à jour…",
        "es": "Preparando el equipo para la actualización…",
    },
    "install_restarting_as_admin": {
        "en": "Restarting Updater CE with administrator rights…",
        "zh-CN": "正在以管理员权限重启 更新程序…",
        "fr": "Redémarrage d'Programme de mise à jour avec les droits d'administrateur…",
        "es": "Reiniciando Actualizador con permisos de administrador…",
    },
    "sp_gui_no_cached_firmware": {
        "zh-CN": "未找到上次安装尝试缓存的固件包，或缺少所需的分区镜像。请在打开 SP Flash Tool 之前，先选择或下载一个固件版本。",
        "en": "No cached firmware package from a previous install attempt was found, or required partition images are missing. Please select or download a software release first before opening SP Flash Tool.",
        "fr": "Aucun micrologiciel mis en cache lors d'une tentative précédente n'a été trouvé, ou des images de partition requises sont manquantes. Veuillez d'abord sélectionner ou télécharger une version avant d'ouvrir SP Flash Tool.",
        "es": "No se encontró ningún paquete de firmware en caché de un intento anterior, o faltan imágenes de partición requeridas. Por favor, selecciona o descarga una versión de software primero antes de abrir SP Flash Tool.",
    },
    "flash_banner_wait": {
        "zh-CN": "等待设备接入",
        "en": "Waiting for device",
        "fr": "En attente de l'appareil",
        "es": "Esperando el dispositivo",
    },
    "flash_banner_ready": {
        "zh-CN": "已就绪，可开始刷机",
        "en": "Ready to flash",
        "fr": "Prêt à flasher",
        "es": "Listo para flashear",
    },
    "flash_banner_flashing": {
        "zh-CN": "正在刷机",
        "en": "Install in Progress",
        "fr": "Installation en cours",
        "es": "Instalación en curso",
    },
    "flash_banner_done": {
        "zh-CN": "安装完成",
        "en": "Install complete",
        "fr": "Installation terminée",
        "es": "Instalación completada",
    },
    "flash_conn_waiting": {
        "zh-CN": "等待连接",
        "en": "Waiting",
        "fr": "En attente",
        "es": "En espera",
    },
    "flash_conn_connected": {
        "zh-CN": "已连接",
        "en": "Connected",
        "fr": "Connecté",
        "es": "Conectado",
    },
    "flash_no_device": {
        "zh-CN": "未检测到设备",
        "en": "No device detected",
        "fr": "Aucun appareil détecté",
        "es": "No se detectó ningún dispositivo",
    },

    # Steps
    "step_extract": {
        "zh-CN": "解压固件包",
        "en": "Extracting",
        "fr": "Extraction",
        "es": "Extrayendo",
    },
    "step_wait": {
        "zh-CN": "等待设备",
        "en": "Waiting",
        "fr": "En attente",
        "es": "En espera",
    },
    "step_detect": {
        "zh-CN": "设备识别",
        "en": "Detect",
        "fr": "Détection",
        "es": "Detectar",
    },
    "step_download_da": {
        "zh-CN": "下载 DA",
        "en": "Downloading DA",
        "fr": "Téléchargement du DA",
        "es": "Descargando DA",
    },
    "step_download_bl": {
        "zh-CN": "下载引导程序",
        "en": "Downloading bootloader",
        "fr": "Téléchargement du bootloader",
        "es": "Descargando bootloader",
    },
    "step_erase": {
        "zh-CN": "擦除旧分区",
        "en": "Erase",
        "fr": "Effacement",
        "es": "Borrar",
    },
    "step_write": {
        "zh-CN": "写入镜像",
        "en": "Writing image",
        "fr": "Écriture de l'image",
        "es": "Escribiendo imagen",
    },
    "step_done": {
        "zh-CN": "完成",
        "en": "Done",
        "fr": "Terminé",
        "es": "Listo",
    },

    # Status tags
    "status_idle": {
        "zh-CN": "空闲",
        "en": "Idle",
        "fr": "Inactif",
        "es": "Inactivo",
    },
    "status_selected": {
        "zh-CN": "已选择",
        "en": "Selected",
        "fr": "Sélectionné",
        "es": "Seleccionado",
    },
    "status_not_selected": {
        "zh-CN": "未选择",
        "en": "Not Selected",
        "fr": "Non sélectionné",
        "es": "No seleccionado",
    },
    "status_connected": {
        "zh-CN": "已连接",
        "en": "Connected",
        "fr": "Connecté",
        "es": "Conectado",
    },
    "status_disconnected": {
        "zh-CN": "已断开",
        "en": "Disconnected",
        "fr": "Déconnecté",
        "es": "Desconectado",
    },
    "status_flashing": {
        "zh-CN": "刷机中",
        "en": "Install in Progress",
        "fr": "Installation en cours",
        "es": "Instalación en curso",
    },
    "status_complete": {
        "zh-CN": "完成",
        "en": "Complete",
        "fr": "Terminé",
        "es": "Completado",
    },
    "status_failed": {
        "zh-CN": "失败",
        "en": "Failed",
        "fr": "Échec",
        "es": "Falló",
    },
    "status_retrying": {
        "zh-CN": "重试中",
        "en": "Retrying",
        "fr": "Nouvelle tentative",
        "es": "Reintentando",
    },

    # Error page
    "err_flash_title": {
        "zh-CN": "刷机失败",
        "en": "Flash Failed",
        "fr": "Échec du flashing",
        "es": "Error de flashing",
    },
    "err_usb_title": {
        "zh-CN": "USB 已断开",
        "en": "USB Disconnected",
        "fr": "USB déconnecté",
        "es": "USB desconectado",
    },
    "err_device_title": {
        "zh-CN": "未检测到设备",
        "en": "No Device Detected",
        "fr": "Aucun appareil détecté",
        "es": "No se detectó el dispositivo",
    },
    "err_btn_retry": {
        "zh-CN": "重新刷机",
        "en": "Retry Flash",
        "fr": "Réessayer le flashing",
        "es": "Reintentar flashing",
    },
    "err_btn_reconnect": {
        "zh-CN": "重新连接设备",
        "en": "Reconnect Device",
        "fr": "Reconnecter l'appareil",
        "es": "Reconectar dispositivo",
    },
    "err_btn_reselect": {
        "zh-CN": "重新选包",
        "en": "Reselect Package",
        "fr": "Rechoisir le paquet",
        "es": "Volver a seleccionar paquete",
    },
    "err_error_code": {
        "zh-CN": "错误码",
        "en": "Error Code",
        "fr": "Code d'erreur",
        "es": "Código de error",
    },
    "err_failed_at": {
        "zh-CN": "失败于",
        "en": "Failed at",
        "fr": "Échec à",
        "es": "Falló en",
    },
    "err_retry_count": {
        "zh-CN": "重试次数",
        "en": "Retry Count",
        "fr": "Nombre de tentatives",
        "es": "Número de reintentos",
    },
    "err_view_log": {
        "zh-CN": "查看诊断",
        "en": "View Diagnostics",
        "fr": "Voir les diagnostics",
        "es": "Ver diagnóstico",
    },
    "err_restart_flash": {
        "zh-CN": "从头重新刷机",
        "en": "Restart flash from beginning",
        "fr": "Redémarrer le flashing depuis le début",
        "es": "Reiniciar el flashing desde el principio",
    },

    # Retry page
    "retry_title": {
        "zh-CN": "重新刷机中",
        "en": "Retrying",
        "fr": "Nouvelle tentative",
        "es": "Reintentando",
    },
    "retry_info_title": {
        "zh-CN": "重新刷机信息",
        "en": "Retry Info",
        "fr": "Informations de nouvelle tentative",
        "es": "Información del reintento",
    },
    "retry_count_fmt": {
        "zh-CN": "第 {n} 次",
        "en": "Attempt {n}",
        "fr": "Tentative {n}",
        "es": "Intento {n}",
    },
    "retry_btn_cancel": {
        "zh-CN": "取消重试",
        "en": "Cancel Retry",
        "fr": "Annuler la tentative",
        "es": "Cancelar reintento",
    },
    "flash_cancel_title": {
        "zh-CN": "取消确认",
        "en": "Confirm Cancel",
        "fr": "Confirmer l'annulation",
        "es": "Confirmar cancelación",
    },
    "flash_cancel_msg": {
        "zh-CN": "当前正在执行刷机流程。是否取消当前刷机并重新开始？",
        "en": "A flash process is currently running. Do you want to cancel it and start a new one?",
        "fr": "Un processus de flashing est en cours. Voulez-vous l'annuler et recommencer ?",
        "es": "Se está ejecutando un proceso de flashing. ¿Quieres cancelarlo y empezar de nuevo?",
    },
    # The interruption card asks the question the two round buttons answer:
    # stop the run, or go back to it.
    "install_stop_check": {
        "zh-CN": "要取消安装吗？",
        "en": "Would you like to cancel the install?",
        "fr": "Voulez-vous annuler l'installation ?",
        "es": "¿Quieres cancelar la instalación?",
        "de": "Möchten Sie die Installation abbrechen?",
        "ja": "インストールをキャンセルしますか？",
    },
    "install_stop_check_mediatek": {
        "zh-CN": "要取消固件安装吗？",
        "en": "Would you like to cancel the firmware install?",
        "fr": "Voulez-vous annuler l'installation du firmware ?",
        "es": "¿Quieres cancelar la instalación del firmware?",
        "de": "Möchten Sie die Firmware-Installation abbrechen?",
        "ja": "ファームウェアのインストールをキャンセルしますか？",
    },
    "download_stop_check": {
        "zh-CN": "要取消下载吗？",
        "en": "Would you like to cancel the download?",
        "fr": "Voulez-vous annuler le téléchargement ?",
        "es": "¿Quieres cancelar la descarga?",
        "de": "Möchten Sie den Download abbrechen?",
        "ja": "ダウンロードをキャンセルしますか？",
    },
    "download_stop_check_mediatek": {
        "zh-CN": "要取消固件下载吗？",
        "en": "Would you like to cancel the firmware download?",
        "fr": "Voulez-vous annuler le téléchargement du firmware ?",
        "es": "¿Quieres cancelar la descarga del firmware?",
        "de": "Möchten Sie den Firmware-Download abbrechen?",
        "ja": "ファームウェアのダウンロードをキャンセルしますか？",
    },
    "inline_go_back": {
        "zh-CN": "返回",
        "en": "Go back",
        "fr": "Retour",
        "es": "Volver",
        "de": "Zurück",
        "ja": "戻る",
    },
    "install_interrupt_cancel": {
        "zh-CN": "取消安装",
        "en": "Cancel Install",
        "fr": "Annuler",
        "es": "Cancelar",
        "de": "Abbrechen",
        "ja": "中止",
    },
    "install_interrupt_back": {
        "zh-CN": "返回",
        "en": "Back",
        "fr": "Retour",
        "es": "Volver",
        "de": "Zurück",
        "ja": "戻る",
    },
    "flash_close_confirm": {
        "zh-CN": "您确定要停止安装并关闭 {app} 吗？",
        "en": "Are you sure you want to stop the install and close {app}?",
        "fr": "Êtes-vous sûr de vouloir arrêter l'installation et fermer {app} ?",
        "es": "¿Seguro que desea detener la instalación y cerrar {app}?",
    },

    # Diagnostics / dialogs
    "log_center": {
        "zh-CN": "诊断",
        "en": "Diagnostics",
        "fr": "Diagnostics",
        "es": "Diagnóstico",
    },
    "log_no_entries": {
        "zh-CN": "暂无诊断记录",
        "en": "No diagnostics entries",
        "fr": "Aucune entrée de diagnostic",
        "es": "Sin entradas de diagnóstico",
    },
    "log_filter_all": {
        "zh-CN": "全部日志 (整合)",
        "en": "All Logs (Unified)",
        "fr": "Tous les journaux (unifiés)",
        "es": "Todos los registros (unificado)",
    },
    "log_filter_sp": {
        "zh-CN": "SP Flash Tool 输出",
        "en": "SP Flash Tool Output",
        "fr": "Sortie SP Flash Tool",
        "es": "Salida de SP Flash Tool",
    },
    "log_filter_mtk": {
        "zh-CN": "MTKClient 输出",
        "en": "MTKClient Output",
        "fr": "Sortie MTKClient",
        "es": "Salida de MTKClient",
    },
    "log_filter_gui_mediatek": {
        "zh-CN": "MediaTek 安装工具（应用日志）",
        "en": "MediaTek Installer (App)",
        "fr": "Installateur MediaTek (Application)",
        "es": "Instalador MediaTek (Aplicación)",
    },
    "log_filter_gui": {
        "zh-CN": "更新程序 (应用日志)",
        "en": "Updater CE (App)",
        "fr": "Programme de mise à jour (Application)",
        "es": "Actualizador (Aplicación)",
    },
    "log_session_label": {
        "zh-CN": "安装记录：",
        "en": "Install:",
        "fr": "Installation :",
        "es": "Instalación:",
    },
    "log_session_tip": {
        "zh-CN": "选择要查看的安装记录（按时间筛选）",
        "en": "Jump to the log of a recorded install attempt",
        "fr": "Aller au journal d'une tentative d'installation",
        "es": "Ir al registro de una instalación concreta",
    },
    "log_session_all": {
        "zh-CN": "全部安装记录",
        "en": "All installs",
        "fr": "Toutes les installations",
        "es": "Todas las instalaciones",
    },
    "log_session_ok": {
        "zh-CN": "成功",
        "en": "Success",
        "fr": "Réussi",
        "es": "Correcta",
    },
    "log_session_failed": {
        "zh-CN": "失败",
        "en": "Failed",
        "fr": "Échec",
        "es": "Fallida",
    },
    "log_session_running": {
        "zh-CN": "进行中",
        "en": "In progress",
        "fr": "En cours",
        "es": "En curso",
    },
    "log_session_unknown_package": {
        "zh-CN": "未知软件包",
        "en": "unknown package",
        "fr": "paquet inconnu",
        "es": "paquete desconocido",
    },
    "log_range_from": {
        "zh-CN": "从：",
        "en": "From:",
        "fr": "Depuis :",
        "es": "Desde:",
    },
    "log_range_from_tip": {
        "zh-CN": "显示此时间之后的日志",
        "en": "Show log lines from this moment on",
        "fr": "Afficher les lignes à partir de ce moment",
        "es": "Mostrar las líneas desde este momento",
    },
    "log_range_to": {
        "zh-CN": "至：",
        "en": "To:",
        "fr": "Jusqu'à :",
        "es": "Hasta:",
    },
    "log_range_to_tip": {
        "zh-CN": "显示此时间之前的日志",
        "en": "Show log lines up to this moment",
        "fr": "Afficher les lignes jusqu'à ce moment",
        "es": "Mostrar las líneas hasta este momento",
    },
    "log_problems_only": {
        "zh-CN": "仅显示故障",
        "en": "Problems only",
        "fr": "Problèmes uniquement",
        "es": "Solo problemas",
    },
    "log_problems_tip": {
        "zh-CN": "仅显示错误、失败与警告行（含其后续堆栈信息）",
        "en": "Show errors, failures and warnings (with the lines that follow them)",
        "fr": "Afficher erreurs, échecs et avertissements (avec les lignes suivantes)",
        "es": "Mostrar errores, fallos y avisos (con las líneas siguientes)",
    },
    "log_lines_count": {
        "zh-CN": "显示 {shown} / {total} 行",
        "en": "{shown} of {total} lines shown",
        "fr": "{shown} lignes sur {total}",
        "es": "{shown} de {total} líneas",
    },
    "log_lines_total": {
        "zh-CN": "共 {total} 行",
        "en": "{total} lines",
        "fr": "{total} lignes",
        "es": "{total} líneas",
    },
    "log_export_filter": {
        "zh-CN": "导出范围：{desc}",
        "en": "Export filter: {desc}",
        "fr": "Filtre d'export : {desc}",
        "es": "Filtro de exportación: {desc}",
    },
    "log_btn_goto_file": {
        "zh-CN": "打开文件位置",
        "en": "Go To File",
        "fr": "Aller au fichier",
        "es": "Ir al archivo",
    },
    "log_btn_save_file": {
        "zh-CN": "保存文件…",
        "en": "Save File…",
        "fr": "Enregistrer le fichier…",
        "es": "Guardar archivo…",
    },
    "log_btn_copy": {
        "zh-CN": "复制",
        "en": "Copy",
        "fr": "Copier",
        "es": "Copiar",
    },
    "log_save_title": {
        "zh-CN": "保存诊断日志",
        "en": "Save Diagnostic Log",
        "fr": "Enregistrer le journal de diagnostic",
        "es": "Guardar registro de diagnóstico",
    },
    "log_copied_hint": {
        "zh-CN": "已复制到剪贴板",
        "en": "Copied to clipboard",
        "fr": "Copié dans le presse-papiers",
        "es": "Copiado al portapapeles",
    },
    "log_save_success": {
        "zh-CN": "日志已保存到 {path}",
        "en": "Log saved to {path}",
        "fr": "Journal enregistré sous {path}",
        "es": "Registro guardado en {path}",
    },
    "flash_complete": {
        "zh-CN": "刷机完成",
        "en": "Flash Complete",
        "fr": "Flashing terminé",
        "es": "Flashing completado",
    },
    "flash_complete_title": {
        "zh-CN": "刷机完成",
        "en": "Flash Complete",
        "fr": "Flashing terminé",
        "es": "Flashing completado",
    },
    "flash_failed": {
        "zh-CN": "安装失败",
        "en": "Install Failed",
        "fr": "Échec de l'installation",
        "es": "Error de instalación",
        "de": "Installation fehlgeschlagen",
        "ja": "インストールに失敗しました",
    },
    "flash_failed_title": {
        "zh-CN": "安装失败",
        "en": "Install Failed",
        "fr": "Échec de l'installation",
        "es": "Error de instalación",
        "de": "Installation fehlgeschlagen",
        "ja": "インストールに失敗しました",
    },
    "install_failed": {
        "zh-CN": "安装失败",
        "en": "Install Failed",
        "fr": "Échec de l'installation",
        "es": "Error de instalación",
        "de": "Installation fehlgeschlagen",
        "ja": "インストールに失敗しました",
    },
    "install_failed_title": {
        "zh-CN": "安装失败",
        "en": "Install Failed",
        "fr": "Échec de l'installation",
        "es": "Error de instalación",
        "de": "Installation fehlgeschlagen",
        "ja": "インストールに失敗しました",
    },
    "flash_retry_title": {
        "zh-CN": "正在重试",
        "en": "Retrying",
        "fr": "Nouvelle tentative",
        "es": "Reintentando",
    },
    "flash_ready_title": {
        "zh-CN": "准备刷机",
        "en": "Ready to Flash",
        "fr": "Prêt pour le flashing",
        "es": "Listo para flashear",
    },
    "close": {
        "zh-CN": "关闭",
        "en": "Close",
        "fr": "Fermer",
        "es": "Cerrar",
    },
    "drop_firmware_here": {
        "zh-CN": "将固件文件拖放到此处以安装",
        "en": "Drop firmware file here to install",
        "fr": "Déposez le fichier firmware ici pour l'installer",
        "es": "Suelta el archivo de firmware aquí para instalarlo",
        "de": "Firmware-Datei zum Installieren hier ablegen",
        "ja": "インストールするファームウェアファイルをここにドロップ",
    },
    "drop_checking": {
        "zh-CN": "正在检查固件文件…",
        "en": "Checking the firmware file…",
        "fr": "Vérification du fichier firmware…",
        "es": "Comprobando el archivo de firmware…",
        "de": "Firmware-Datei wird geprüft…",
        "ja": "ファームウェアファイルを確認しています…",
    },
    "drop_missing_scatter": {
        "zh-CN": "未找到 scatter 文件，因此没有开始安装。",
        "en": "No scatter file was found, so installation did not start.",
        "fr": "Aucun fichier scatter n'a été trouvé, l'installation n'a donc pas démarré.",
        "es": "No se encontró un archivo scatter, así que la instalación no comenzó.",
        "de": "Es wurde keine Scatter-Datei gefunden, daher wurde die Installation nicht gestartet.",
        "ja": "スキャッターファイルが見つからなかったため、インストールは開始されませんでした。",
    },
    "drop_missing_images": {
        "zh-CN": "scatter 文件列出的部分镜像缺失，因此没有开始安装。",
        "en": "Some images listed in the scatter file are missing, so installation did not start.",
        "fr": "Des images listées dans le fichier scatter sont absentes, l'installation n'a donc pas démarré.",
        "es": "Faltan algunas imágenes listadas en el archivo scatter, así que la instalación no comenzó.",
        "de": "Einige in der Scatter-Datei aufgeführten Images fehlen, daher wurde die Installation nicht gestartet.",
        "ja": "スキャッターファイルに記載されたイメージの一部がないため、インストールは開始されませんでした。",
    },
    "ok": {
        "zh-CN": "知道了",
        "en": "OK",
        "fr": "OK",
        "es": "Aceptar",
    },

    # Donation dialog
    "donate_title": {
        "zh-CN": "支持 更新程序",
        "en": "Support Updater CE",
        "fr": "Soutenir Programme de mise à jour",
        "es": "Apoya a Actualizador",
    },
    "donate_title_mediatek": {
        "zh-CN": "支持 MediaTek 安装工具",
        "en": "Support MediaTek Installer",
        "fr": "Soutenir l'Installateur MediaTek",
        "es": "Apoyar el Instalador MediaTek",
    },
    "donate_goal_fmt": {
        "zh-CN": "<b>您已帮助我们筹集了本月 ${target:.0f} 美元中的 ${raised} 美元</b>",
        "en": "<b>You've helped us raise ${raised} of our ${target:.0f} for this month</b>",
        "fr": "<b>Vous nous avez aidés à collecter ${raised} sur nos ${target:.0f} pour couvrir les frais ce mois-ci</b>",
        "es": "<b>Nos has ayudado a recaudar ${raised} de nuestros ${target:.0f} para este mes</b>",
        "de": "<b>Du hast uns geholfen, ${raised} unserer ${target:.0f} für diesen Monat zu sammeln</b>",
        "ja": "<b>今月の目標 ${target:.0f} のうち ${raised} のご支援をいただきました</b>",
    },
    "donate_goal_status_fmt": {
        "zh-CN": "<b>本月你已帮我们筹集 ${raised}，目标成本 ${target:.0f}</b>",
        "en": "<b>You've helped us raise ${raised} of our ${target:.0f} costs this month</b>",
        "fr": "<b>Vous nous avez aidé à collecter ${raised} sur nos ${target:.0f} de frais ce mois-ci</b>",
        "es": "<b>Nos has ayudado a recaudar ${raised} de nuestros ${target:.0f} de costes este mes</b>",
        "de": "<b>Diesen Monat: ${raised} / ${target:.0f}</b>",
        "ja": "<b>今月：${raised} / ${target:.0f}</b>",
    },
    "err_usb_disconnected_recovery": {
        "en": "Your {model} got disconnected. Please disconnect the USB and use a paperclip / pin to press the button between headphone jack and USB Port, then press next.",
        "zh-CN": "您的 {model} 已断开连接。请断开 USB，并用卡针/回形针按下耳机孔与 USB 接口之间的按钮，然后点击下一步。",
        "fr": "Votre {model} a été déconnecté. Veuillez débrancher l'USB et utiliser un trombone / une aiguille pour appuyer sur le bouton situé entre la prise casque et le port USB, puis appuyez sur Suivant.",
        "es": "Tu {model} se ha desconectado. Desconecta el USB y usa un clip / alfiler para presionar el botón entre la toma de auriculares y el puerto USB, luego pulsa Siguiente.",
        "de": "Ihr {model} wurde getrennt. Bitte trennen Sie USB und drücken Sie mit einer Büroklammer / Nadel die Taste zwischen Kopfhörerbuchse und USB-Anschluss, dann drücken Sie auf Weiter.",
        "ja": "{model}の接続が切断されました。USBを外し、クリップまたはピンでイヤホンジャックとUSBポートの間にあるボタンを押してから、「次へ」を押してください。",
    },
    # Shown instead of the disconnect line when the backend never reached the
    # player at all (SP Flash Tool's 1013 / COM-port error) and the run is
    # switching engines: the failure, the engine taking over, and the reset
    # steps each stand on their own line.
    "err_connect_failover_recovery": {
        "en": "{failed} couldn't connect to your {model}.\n\nUpdater will now try {next} instead.\n\nPlease disconnect the USB and use a paperclip / pin to press the button between headphone jack and USB Port, then press next.",
        "zh-CN": "{failed} 无法连接到您的 {model}。\n\nUpdater 将改用 {next}。\n\n请断开 USB，并用卡针 / 回形针按下耳机孔与 USB 接口之间的按钮，然后点击下一步。",
        "fr": "{failed} n'a pas pu se connecter à votre {model}.\n\nUpdater va maintenant essayer {next} à la place.\n\nVeuillez débrancher l'USB et utiliser un trombone / une aiguille pour appuyer sur le bouton situé entre la prise casque et le port USB, puis appuyez sur Suivant.",
        "es": "{failed} no pudo conectarse a tu {model}.\n\nUpdater ahora usará {next}.\n\nDesconecta el USB y usa un clip / alfiler para presionar el botón entre la toma de auriculares y el puerto USB, luego pulsa Siguiente.",
        "de": "{failed} konnte sich nicht mit Ihrem {model} verbinden.\n\nUpdater versucht jetzt stattdessen {next}.\n\nBitte trennen Sie das USB und drücken Sie mit einer Büroklammer / Nadel die Taste zwischen Kopfhörerbuchse und USB-Anschluss, dann drücken Sie auf Weiter.",
        "ja": "{failed} は{model}に接続できませんでした。\n\n次にアップデーターは代わりに {next} を使用します。\n\nUSB を外し、クリップまたはピンでイヤホンジャックとUSBポートの間にあるボタンを押してから、「次へ」を押してください。",
    },
    "donate_headline": {
        "zh-CN": "它需要 <span style='color:#ff5252; text-decoration:underline;'>你</span>。",
        "en": "It takes <span style='color:#ff5252; text-decoration:underline;'>you</span>.",
        "fr": "Il faut <span style='color:#ff5252; text-decoration:underline;'>vous</span>.",
        "es": "Te necesita a <span style='color:#ff5252; text-decoration:underline;'>ti</span>.",
        "de": "Es braucht <span style='color:#ff5252; text-decoration:underline;'>dich</span>.",
        "ja": "必要なのは<span style='color:#ff5252; text-decoration:underline;'>あなた</span>。",
    },
    "donate_subtitle": {
        "zh-CN": "我们的项目依赖社区捐赠",
        "en": "Our projects rely on donations from the community",
        "fr": "Nos projets dépendent des dons de la communauté",
        "es": "Nuestros proyectos dependen de las donaciones de la comunidad",
        "de": "Unsere Projekte leben von Spenden aus der Community",
        "ja": "プロジェクトはコミュニティの寄付で続いています",
    },
    "donate_this_firmware": {
        "zh-CN": "此固件",
        "en": "this firmware",
        "fr": "ce firmware",
        "es": "este firmware",
    },
    # The note under a finished install. It says what just happened first, then
    # asks — the install is the reason the reader is here, and the ask lands
    # much better once the job is visibly done.
    "donate_completion_headline": {
        "zh-CN": "装好了，<span style='color:#ff5252; text-decoration:underline;'>谢谢你</span>。",
        "en": "All done — thank <span style='color:#ff5252; text-decoration:underline;'>you</span>.",
        "fr": "C'est fait — <span style='color:#ff5252; text-decoration:underline;'>merci</span> à vous.",
        "es": "Listo — <span style='color:#ff5252; text-decoration:underline;'>gracias</span> a ti.",
        "de": "Fertig — <span style='color:#ff5252; text-decoration:underline;'>danke</span> dir.",
        "ja": "完了しました — <span style='color:#ff5252; text-decoration:underline;'>ありがとう</span>。",
    },
    "donate_completion_subtitle": {
        "zh-CN": "{software} 已成功安装到你的 {model}。",
        "en": "{software} is installed on your {model}.",
        "fr": "{software} est installé sur votre {model}.",
        "es": "{software} ya está instalado en tu {model}.",
        "de": "{software} ist auf deinem {model} installiert.",
        "ja": "{model} に {software} をインストールしました。",
    },
    "donate_completion_body": {
        "zh-CN": "更新程序 完全免费，没有广告，也不追踪你——它靠大家的小额捐助运行。如果它今天帮到了你，请我喝杯咖啡，就能让下载和服务器继续跑下去。",
        "en": "Updater CE is free, has no ads and no tracking — it runs on small donations from people like you. If it saved you a job today, buying me a coffee keeps the downloads and the servers running.",
        "fr": "Programme de mise à jour est gratuit, sans publicité ni suivi — il fonctionne grâce aux petits dons de gens comme vous. S'il vous a épargné du travail aujourd'hui, un café permet de payer les téléchargements et les serveurs.",
        "es": "Actualizador es gratis, sin anuncios ni rastreo: funciona con pequeñas donaciones de gente como tú. Si hoy te ahorró trabajo, un café mantiene las descargas y los servidores en marcha.",
        "de": "Updater ist kostenlos, ohne Werbung und ohne Tracking — es lebt von kleinen Spenden von Leuten wie dir. Wenn es dir heute Arbeit erspart hat, hält ein Kaffee die Downloads und Server am Laufen.",
        "ja": "アップデーターは無料で、広告も追跡もありません。皆さんの少額の寄付で動いています。今日、手間が省けたなら、コーヒー一杯がダウンロードとサーバーを支えます。",
    },
    "donate_completion_coffee": {
        "zh-CN": "请我喝杯咖啡",
        "en": "Buy me a coffee",
        "fr": "Offrez-moi un café",
        "es": "Invítame a un café",
        "de": "Spendier mir einen Kaffee",
        "ja": "コーヒーをおごる",
    },
    "donate_intro_success_mediatek": {
        "zh-CN": "我们已将 <b>{software}</b> 安装到您的 <b>{model}</b>。<br><br>你好！我是 Ryan，MediaTek 安装工具的开发者。我自掏腰包承担托管和存储费用，以保持这个安装工具免费开放。如果它帮到了您，请考虑捐赠——这让项目能持续为大家服务。",
        "en": "We've installed <b>{software}</b> on your <b>{model}</b>.<br><br>Hey! I'm Ryan, the developer behind MediaTek Installer. I cover hosting and storage costs out of pocket to keep this installer free and open. If it helped you, please consider donating — it keeps the project alive for everyone.",
        "fr": "Nous avons installé <b>{software}</b> sur votre <b>{model}</b>.<br><br>Salut ! Je suis Ryan, le développeur derrière l'Installateur MediaTek. Je couvre les frais d'hébergement et de stockage de ma poche pour que cet installateur reste gratuit et ouvert. S'il vous a aidé, pensez à faire un don — cela maintient le projet en vie pour tout le monde.",
        "es": "Hemos instalado <b>{software}</b> en tu <b>{model}</b>.<br><br>¡Hola! Soy Ryan, el desarrollador detrás del Instalador MediaTek. Cubro los gastos de alojamiento y almacenamiento de mi bolsillo para mantener este instalador gratis y abierto. Si te ha ayudado, considera hacer una donación: mantiene el proyecto vivo para todos.",
    },
    "donate_intro_general_mediatek": {
        "zh-CN": "你好！我是 Ryan，MediaTek 安装工具的开发者。我自掏腰包支付托管、工具和存储费用，以保持这个安装工具免费。每月维护费用约 200 美元——您的任何支持都能帮助它继续服务下一位用户。",
        "en": "Hey! I'm Ryan, the developer behind MediaTek Installer. I pay for hosting, tooling and storage out of my own pocket to keep this installer free. Monthly upkeep comes to around $200 — any support you give helps keep it alive for the next user.",
        "fr": "Salut ! Je suis Ryan, le développeur derrière l'Installateur MediaTek. Je paie l'hébergement, les outils et le stockage de ma poche pour que cet installateur reste gratuit. L'entretien mensuel s'élève à environ 200 $ — toute aide contribue à le garder en vie pour le prochain utilisateur.",
        "es": "¡Hola! Soy Ryan, el desarrollador detrás del Instalador MediaTek. Pago el alojamiento, las herramientas y el almacenamiento de mi bolsillo para mantener este instalador gratis. El mantenimiento mensual ronda los 200 $; cualquier apoyo ayuda a mantenerlo vivo para el próximo usuario.",
    },
    "donate_intro_success": {
        "zh-CN": "我们已将 <b>{software}</b> 安装到您的 <b>{model}</b>。<br><br>你好！我是 Ryan，更新程序、社区固件档案和主题画廊的开发者。我自掏腰包承担每月约 200 美元的服务器和存储费用，以保持一切免费和开放。如果这个工具帮助了您，请考虑捐赠——这能让这些资源持续为大家服务。",
        "en": "We've installed <b>{software}</b> on your <b>{model}</b>.<br><br>Hey! I'm Ryan, the developer behind Updater CE, the Community Firmware Archive, and the Themes Gallery. I cover our ~$200 monthly server and storage costs out of pocket to keep everything free and open. If this tool helped you, please consider donating — it keeps these resources alive for everyone.",
        "fr": "Nous avons installé <b>{software}</b> sur votre <b>{model}</b>.<br><br>Salut ! Je suis Ryan, le développeur derrière Programme de mise à jour, l'Archive Communautaire de Firmwares et la Galerie de Thèmes. Je couvre nos ~200 $ mensuels de serveur et de stockage de ma poche pour que tout reste gratuit et ouvert. Si cet outil vous a aidé, pensez à faire un don — cela maintient ces ressources en vie pour tout le monde.",
        "es": "Hemos instalado <b>{software}</b> en tu <b>{model}</b>.<br><br>¡Hola! Soy Ryan, el desarrollador detrás de Actualizador, el Archivo Comunitario de Firmwares y la Galería de Temas. Cubro nuestros ~200 $ mensuales de servidor y almacenamiento de mi propio bolsillo para mantener todo gratis y abierto. Si esta herramienta te ha ayudado, considera hacer una donación: mantiene estos recursos vivos para todos.",
    },
    "donate_intro_general": {
        "zh-CN": "你好！我是 Ryan，更新程序、社区固件档案和主题画廊的开发者。我自掏腰包支付服务器托管、固件档案存储和域名续费，以保持一切免费。每月维护费用约 200 美元——您的任何支持都能帮助这些工具继续为下一位用户服务。",
        "en": "Hey! I'm Ryan, the developer behind Updater CE, the Community Firmware Archive, and the Themes Gallery. I pay for server hosting, firmware archive storage, and domain renewals out of my own pocket to keep everything free. Monthly upkeep comes to around $200 — any support you give helps keep these tools alive for the next owner.",
        "fr": "Salut ! Je suis Ryan, le développeur derrière Programme de mise à jour, l'Archive Communautaire de Firmwares et la Galerie de Thèmes. Je paie l'hébergement du serveur, le stockage des firmwares et les renouvellements de domaine de ma poche pour que tout reste gratuit. L'entretien mensuel s'élève à environ 200 $ — toute aide que vous apportez contribue à garder ces outils en vie pour le prochain utilisateur.",
        "es": "¡Hola! Soy Ryan, el desarrollador detrás de Actualizador, el Archivo Comunitario de Firmwares y la Galería de Temas. Pago el alojamiento del servidor, el almacenamiento del archivo de firmwares y las renovaciones de dominio de mi bolsillo para mantener todo gratis. El mantenimiento mensual ronda los 200 $; cualquier apoyo que des ayuda a mantener estas herramientas vivas para el próximo usuario.",
    },
    "donate_supporter": {
        "zh-CN": "支持者",
        "en": "Supporter",
        "fr": "Donateur",
        "es": "Donante",
    },
    "donate_method_generic": {
        "zh-CN": "捐赠",
        "en": "Donation",
        "fr": "Don",
        "es": "Donación",
    },
    "donate_when_today": {
        "zh-CN": "今天",
        "en": "Today",
        "fr": "Aujourd\u2019hui",
        "es": "Hoy",
    },
    "donate_when_yesterday": {
        "zh-CN": "昨天",
        "en": "Yesterday",
        "fr": "Hier",
        "es": "Ayer",
    },
    "donate_when_day": {
        "zh-CN": "{day}",
        "en": "on {day}",
        "fr": "{day}",
        "es": "{day}",
    },
    "donate_when_days": {
        "zh-CN": "{count}\u5929\u524d",
        "en": "{count} days ago",
        "fr": "il y a {count} jours",
        "es": "hace {count} d\u00edas",
    },
    "donate_when_week": {
        "zh-CN": "\u4e00\u5468\u524d",
        "en": "a week ago",
        "fr": "il y a une semaine",
        "es": "hace una semana",
    },
    "donate_when_weeks": {
        "zh-CN": "{count}\u5468\u524d",
        "en": "{count} weeks ago",
        "fr": "il y a {count} semaines",
        "es": "hace {count} semanas",
    },
    "donate_when_month": {
        "zh-CN": "\u4e00\u4e2a\u6708\u524d",
        "en": "a month ago",
        "fr": "il y a un mois",
        "es": "hace un mes",
    },
    "donate_when_months": {
        "zh-CN": "{count}\u4e2a\u6708\u524d",
        "en": "{count} months ago",
        "fr": "il y a {count} mois",
        "es": "hace {count} meses",
    },
    "donate_when_year": {
        "zh-CN": "\u4e00\u5e74\u524d",
        "en": "over a year ago",
        "fr": "il y a plus d\u2019un an",
        "es": "hace m\u00e1s de un a\u00f1o",
    },
    "donate_ticker_fmt": {
        "zh-CN": "<b>{anchor}</b> {when} 通过 {method} 捐赠了 ${amount}",
        "en": "<b>{anchor}</b> donated ${amount} by {method} — {when}",
        "fr": "<b>{anchor}</b> a donné ${amount} via {method} \u2014 {when}",
        "es": "<b>{anchor}</b> donó ${amount} mediante {method} — {when}",
    },
    "donate_ticker_status_fmt": {
        "zh-CN": "<b>{anchor}</b> 捐赠了 ${amount}{when}",
        "en": "<b>{anchor}</b> donated ${amount}{when}",
        "fr": "<b>{anchor}</b> a donné ${amount}{when}",
        "es": "<b>{anchor}</b> donó ${amount}{when}",
        "de": "<b>{anchor}</b> spendete ${amount}{when}",
        "ja": "<b>{anchor}</b> ${amount} 寄付{when}",
    },
    "donate_kofi": {
        "zh-CN": "请我们喝咖啡 (Buy Us A Coffee)",
        "en": "Buy Us A Coffee",
        "fr": "Offrez-nous un café (Buy Us A Coffee)",
        "es": "Cómpranos un café (Buy Us A Coffee)",
    },
    "donate_paypal": {
        "zh-CN": "通过 PayPal 捐赠",
        "en": "Donate by PayPal",
        "fr": "Faire un don via PayPal",
        "es": "Donar con PayPal",
    },
    "donate_revolut": {
        "zh-CN": "通过 Revolut 捐赠",
        "en": "Donate by Revolut",
        "fr": "Faire un don via Revolut",
        "es": "Donar con Revolut",
    },
    "donate_patreon": {
        "zh-CN": "通过 Patreon 捐赠",
        "en": "Donate by Patreon",
        "fr": "Faire un don via Patreon",
        "es": "Donar con Patreon",
    },
    "donate_honeygain": {
        "zh-CN": "免费支持：在 Honeygain 上共享 100MB",
        "en": "Contribute for free by sharing 100MB on Honeygain",
        "fr": "Contribuez gratuitement en partageant 100 Mo sur Honeygain",
        "es": "Contribuye gratis compartiendo 100 MB en Honeygain",
    },
    "donate_crypto_toggle": {
        "zh-CN": "显示加密货币选项 (BTC, ETH, SHIB)",
        "en": "Show Crypto Options (BTC, ETH, SHIB)",
        "fr": "Afficher les options crypto (BTC, ETH, SHIB)",
        "es": "Mostrar opciones de cripto (BTC, ETH, SHIB)",
    },
    "donate_crypto_hide": {
        "zh-CN": "隐藏加密货币选项",
        "en": "Hide Crypto Options",
        "fr": "Masquer les options crypto",
        "es": "Ocultar opciones de cripto",
    },
    "donate_dont_ask": {
        "zh-CN": "刷机成功后不再提示我",
        "en": "Don't ask me again after successful firmware installs",
        "fr": "Ne plus me demander après une installation réussie",
        "es": "No volver a preguntarme tras instalaciones correctas",
    },
    "donate_thanks_status": {
        "zh-CN": "感谢所有支持者！",
        "en": "Thanks, supporters!",
        "fr": "Merci aux donateurs !",
        "es": "¡Gracias, donantes!",
        "de": "Danke an alle Unterstützer!",
        "ja": "支援者の皆様、ありがとう！",
    },
    "donate_thanks": {
        "zh-CN": "感谢所有支持者与贡献者！",
        "en": "Thanks, supporters!",
        "fr": "Merci aux donateurs !",
        "es": "¡Gracias, donantes!",
    },
    "donate_thanks_status": {
        "zh-CN": "感谢所有支持者！",
        "en": "Thanks, supporters!",
        "fr": "Merci aux donateurs !",
        "es": "¡Gracias, donantes!",
        "de": "Danke an alle Unterstützer!",
        "ja": "支援者の皆様、ありがとう！",
    },
    "donate_coffee_pitch": {
        "zh-CN": "嗨，我是 Ryan。如果我的工具 {app} 对你有帮助，可以请我喝杯咖啡。",
        "en": "Hi, Ryan here, if you found my tool {app} helpful, you can buy me a coffee.",
        "fr": "Bonjour, ici Ryan. Si mon outil {app} vous a été utile, vous pouvez m'offrir un café.",
        "es": "Hola, soy Ryan. Si mi herramienta {app} te ha resultado útil, puedes invitarme a un café.",
        "de": "Hi, hier ist Ryan. Wenn dir mein Tool {app} geholfen hat, kannst du mir einen Kaffee spendieren.",
        "ja": "こんにちは、Ryan です。{app} が役に立ったら、コーヒーをご馳走してもらえると嬉しいです。",
    },
    "donate_coffee_btn": {
        "zh-CN": "请我喝咖啡",
        "en": "Buy me a coffee",
        "fr": "Offrez-moi un café",
        "es": "Invítame a un café",
        "de": "Spendier mir einen Kaffee",
        "ja": "コーヒーをご馳走する",
    },
    "donate_buy_coffee": {
        "zh-CN": "请我们喝咖啡",
        "en": "Buy us a coffee",
        "fr": "Offrez-nous un café",
        "es": "Invítanos a un café",
        "de": "Spendier uns einen Kaffee",
        "ja": "コーヒーをご馳走してください",
    },
    "donate_not_now": {
        "zh-CN": "以后再说",
        "en": "Not now",
        "fr": "Plus tard",
        "es": "Ahora no",
        "de": "Nicht jetzt",
        "ja": "今はしない",
    },
    "donate_dont_ask_link": {
        "zh-CN": "不再询问",
        "en": "Don't ask me again",
        "fr": "Ne plus me demander",
        "es": "No volver a preguntar",
        "de": "Nicht mehr fragen",
        "ja": "今後は表示しない",
    },
    "donate_copied": {
        "zh-CN": "{label} 地址已复制",
        "en": "{label} address copied",
        "fr": "Adresse {label} copiée",
        "es": "Dirección de {label} copiada",
    },
    "donate_appeal_costs_for_year": {
        "en": "If everyone using this today donated, it would keep this project going for a year",
        "zh-CN": "如果今天使用本工具的每个人都捐赠一点，就足以支付我们一整年的费用",
        "fr": "Si chaque personne utilisant l'application aujourd'hui donnait, nos frais seraient couverts pour un an",
        "es": "Si todos los que usan esto hoy donaran, cubriría nuestros gastos durante un año",
        "de": "Wenn jeder, der dies heute nutzt, spenden würde, wären unsere Kosten für ein ganzes Jahr gedeckt",
        "ja": "今日これを使っている全員が寄付してくだされば、1年分の運用費が賄えます",
    },
    "donate_support_author": {
        "en": "Ryan Specter & Community Contributors",
        "zh-CN": "Ryan Specter 与社区贡献者",
        "fr": "Ryan Specter et les contributeurs de la communauté",
        "es": "Ryan Specter y colaboradores de la comunidad",
        "is": "Ryan Specter og framlagsfólk samfélagsins",
        "ru": "Райан Спектер и участники сообщества",
        "uk": "Райан Спектер та учасники спільноти",
        "bg": "Райън Спектър и общностни сътрудници",
        "el": "Ράιαν Σπέκτερ και συνεισφέροντες της κοινότητας",
        "ar": "رايان سبيكتر والمساهمون من المجتمع",
        "fa": "رایان اسپکتر و مشارکت‌کنندگان جامعه",
        "iw": "ריאן ספקטר ותורמי הקהילה",
        "ja": "Ryan Specter（ライアン・スペクター）とコミュニティの貢献者",
        "ko": "라이언 스펙터 및 커뮤니티 기여자",
        "hi": "रयान स्पेक्टर और समुदाय के योगदानकर्ता",
        "th": "ไรอัน สเปกเตอร์ และผู้ร่วมพัฒนาชุมชน",
        "hy": "Ռայան Սպեկտեր և համայնքի ներդրողներ",
        "ka": "რაიან სპექტერი და საზოგადოების კონტრიბუტორები",
        "am": "ራያን ስፔክተር እና የማህበረሰብ አስተዋፅዖ አበርካቾች",
        "ta": "ரயன் ஸ்பெக்டர் மற்றும் சமூகப் பங்களிப்பாளர்கள்",
        "bn": "রায়ান স্পেক্টার ও কমিউনিটির অবদানকারীরা",
        "gu": "રાયન સ્પેક્ટર અને સમુદાયના યોગદાનકર્તાઓ",
        "ckb": "ڕایان سپێکتەر و بەشدارانی کۆمەڵگە",
        "ur": "ریان اسپیکٹر اور کمیونٹی کے معاون",
    },
    "donate_support_role": {
        "en": "Lead Developer · Updater CE & Community Firmware Project",
        "zh-CN": "首席开发者 · Updater CE 与社区固件项目",
        "fr": "Développeur principal · Updater CE et projet de micrologiciel communautaire",
        "es": "Desarrollador principal · Updater CE y proyecto de firmware comunitario",
        "is": "Aðalforritari · Updater CE og Community Firmware verkefnið",
    },
    "donate_support_appeal_lead": {
        "en": "Donations directly help me support my family and fund the test devices",
        "zh-CN": "您的捐赠直接帮助我养家糊口，并为测试设备提供资金",
        "fr": "Les dons m'aident directement à subvenir aux besoins de ma famille et à financer les appareils de test",
        "es": "Las donaciones me ayudan directamente a mantener a mi familia y a financiar los dispositivos de prueba",
        "de": "Spenden helfen mir direkt, meine Familie zu unterstützen und die Testgeräte zu finanzieren",
        "ja": "皆様の寄付は、家族を支え、テスト用機器を購入するための大切な支援になります",
    },
    "donate_support_appeal_followup": {
        "en": "that make this free service possible for everyone. If this tool saved you time or rescued your player today, please consider a small donation — it truly means the world to us.",
        "zh-CN": "这些设备让这项免费服务得以惠及所有人。如果这个工具今天为您节省了时间或挽救了您的播放器，请考虑小额捐赠——这对我们意义重大。",
        "fr": "qui rendent ce service gratuit possible pour tous. Si cet outil vous a fait gagner du temps ou a sauvé votre lecteur aujourd'hui, pensez à faire un petit don — cela compte énormément pour nous.",
        "es": "que hacen posible este servicio gratuito para todos. Si esta herramienta te ahorró tiempo o salvó tu reproductor hoy, considera hacer una pequeña donación: significa muchísimo para nosotros.",
        "de": "die diesen kostenlosen Dienst für alle ermöglichen. Wenn dir dieses Tool heute Zeit gespart oder deinen Player gerettet hat, erwäge bitte eine kleine Spende — sie bedeutet uns sehr viel.",
        "ja": "それらが、誰もが利用できる無料サービスを支えています。このツールが今日、時間の節約やプレーヤーの復旧に役立ったなら、少額でもご寄付をご検討ください。皆様のご支援は私たちにとって何より大切です。",
    },
    "nav_help": {
        "en": "Help",
        "zh-CN": "帮助",
        "fr": "Aide",
        "es": "Ayuda",
        "de": "Hilfe",
        "ja": "ヘルプ",
    },
    "help_discord_tip": {
        "en": "Join our Discord",
        "zh-CN": "加入我们的 Discord",
        "fr": "Rejoignez notre Discord",
        "es": "Únete a nuestro Discord",
        "de": "Tritt unserem Discord bei",
        "ja": "Discord に参加する",
    },
    "help_reddit_tip": {
        "en": "Join our Reddit community",
        "zh-CN": "加入我们的 Reddit 社区",
        "fr": "Rejoignez notre communauté Reddit",
        "es": "Únete a nuestra comunidad de Reddit",
        "de": "Tritt unserer Reddit-Community bei",
        "ja": "Reddit コミュニティに参加する",
    },
    "help_github_tip": {
        "en": "Get help on GitHub",
        "zh-CN": "在 GitHub 上获取帮助",
        "fr": "Obtenir de l'aide sur GitHub",
        "es": "Obtén ayuda en GitHub",
        "de": "Hilfe auf GitHub erhalten",
        "ja": "GitHub でヘルプを見る",
    },
    "community_discord": {
        "en": "Discord",
        "zh-CN": "Discord",
        "fr": "Discord",
        "es": "Discord",
        "de": "Discord",
        "ja": "Discord",
    },
    "community_reddit": {
        "en": "Reddit",
        "zh-CN": "Reddit",
        "fr": "Reddit",
        "es": "Reddit",
        "de": "Reddit",
        "ja": "Reddit",
    },
    "donate_appeal_family_and_time": {
        "en": "Your donation directly helps me support my family and fund the countless hours put into this project",
        "zh-CN": "您的捐赠直接帮助我养家糊口，并支持我为本项目倾注的大量心血与时间",
        "fr": "Votre don m'aide directement à subvenir aux besoins de ma famille et finance les innombrables heures consacrées au projet",
        "es": "Tu donación me ayuda directamente a mantener a mi familia y financia las incontables horas dedicadas al proyecto",
        "de": "Deine Spende hilft mir direkt, meine Familie zu ernähren und die unzähligen Stunden in dieses Projekt zu finanzieren",
        "ja": "皆様の寄付が家族の生活を支え、本プロジェクトに注ぐ膨大な時間と労力の糧となります",
    },
    "donate_appeal_human_touch": {
        "en": "Built by an independent developer, not a corporation. A small donation helps with hosting, test devices, and family life",
        "zh-CN": "由独立开发者倾力打造而非大公司运作。一份小小的赞助能为服务器、测试样机和家庭生活带来极大支持",
        "fr": "Conçu par un développeur indépendant, pas une entreprise. Un petit don aide pour l'hébergement, le matériel et la vie de famille",
        "es": "Creado por un desarrollador independiente, no por una gran empresa. Una pequeña ayuda cubre servidores, dispositivos y el sustento familiar",
        "de": "Von einem unabhängigen Entwickler geschaffen, keinem Konzern. Eine kleine Spende hilft bei Hosting, Testgeräten und Familienunterhalt",
        "ja": "企業ではなく一人の個人開発者が作っています。少額の寄付がサーバー費、検証用端末、そして家族の支えになります",
    },
    "donate_appeal_keep_going": {
        "en": "If Updater CE saved your player today, consider donating — it pays for servers, test hardware, and puts food on our table",
        "zh-CN": "如果本工具今天成功救回了您的播放器，请考虑支持一下——这能帮助支付服务器费用并维持全家生计",
        "fr": "Si Programme de mise à jour a sauvé votre appareil aujourd'hui, pensez à donner : cela paie les serveurs, le matériel et nourrit notre famille",
        "es": "Si Actualizador revivió tu reproductor hoy, considera apoyarnos: financia servidores, hardware de prueba y comida en nuestra mesa",
        "de": "Wenn Updater deinen Player heute gerettet hat, denke über eine Spende nach — sie bezahlt Server, Hardware und unser Essen",
        "ja": "今日 アップデーターがお役に立てたなら、ぜひ温かいご支援を。サーバー代、検証機器代、そして日々の暮らしを支えます",
    },
    "donate_appeal_always_free": {
        "en": "This app will always be free, donations are appreciated",
        "zh-CN": "本应用将永远免费，欢迎并感谢您的捐赠支持",
        "fr": "Cette application sera toujours gratuite, les dons sont grandement appréciés",
        "es": "Esta aplicación siempre será gratuita, agradecemos enormemente las donaciones",
        "de": "Diese App bleibt immer kostenlos, Spenden werden sehr geschätzt",
        "ja": "このアプリはずっと無料です。温かいご支援に感謝いたします",
    },
    "donate_appeal_community_archive": {
        "en": "Contribute to the community firmware archive",
        "zh-CN": "支持社区固件归档计划",
        "fr": "Contribuez à l'archive communautaire de micrologiciels",
        "es": "Contribuye al archivo comunitario de firmwares",
        "de": "Unterstütze das Community-Firmware-Archiv",
        "ja": "コミュニティファームウェアアーカイブの維持にご協力ください",
    },
    "donate_appeal_what_innioasis_dont": {
        "en": "Help us do what Innioasis don't",
        "zh-CN": "帮助我们做到原厂不愿做的事",
        "fr": "Aidez-nous à faire ce qu'Innioasis ne fait pas",
        "es": "Ayúdanos a hacer lo que Innioasis no hace",
        "de": "Hilf uns zu tun, was Innioasis versäumt",
        "ja": "メーカーが放置した課題を、共に解決するために。",
    },
    "support_page_cycle_story": {
        "en": "Why we do this ↻",
        "zh-CN": "了解更多初衷 ↻",
        "fr": "Pourquoi nous faisons cela ↻",
        "es": "Por qué hacemos esto ↻",
        "de": "Warum wir das tun ↻",
        "ja": "私たちが活動を続ける理由 ↻",
    },
    "support_badge_preservation": {
        "en": "Hardware Preservation",
        "zh-CN": "硬件留存与适配",
        "fr": "Préservation du matériel",
        "es": "Preservación del hardware",
        "de": "Hardware-Erhaltung",
        "ja": "ハードウェア保護",
    },
    "support_badge_infrastructure": {
        "en": "Independent Infrastructure",
        "zh-CN": "独立固件基础设施",
        "fr": "Infrastructure indépendante",
        "es": "Infraestructura independiente",
        "de": "Unabhängige Infrastruktur",
        "ja": "独自の配信インフラ",
    },
    "support_badge_freedom": {
        "en": "Player Freedom & Rockbox",
        "zh-CN": "自由体验与 Rockbox",
        "fr": "Liberté d'écoute & Rockbox",
        "es": "Libertad y Rockbox",
        "de": "Player-Freiheit & Rockbox",
        "ja": "自由な再生と Rockbox",
    },
    "support_badge_mission": {
        "en": "The Community Mission",
        "zh-CN": "社区初心与使命",
        "fr": "La mission communautaire",
        "es": "Misión comunitaria",
        "de": "Unsere Community-Mission",
        "ja": "コミュニティの使命",
    },
    "support_story_1_headline": {
        "en": "When manufacturers move on, the community steps up.",
        "zh-CN": "当原厂停止维护，社区为您撑起一片天。",
        "fr": "Quand le fabricant abandonne, la communauté prend le relais.",
        "es": "Cuando los fabricantes lo dejan, la comunidad da el paso.",
        "de": "Wenn Hersteller aufgeben, springt die Community ein.",
        "ja": "メーカーが見限っても、コミュニティが支え続けます。",
    },
    "support_story_1_lead": {
        "en": "Protecting your device from silent hardware traps and broken revisions.",
        "zh-CN": "让您的播放器免受静默硬件改版与暗病固件的困扰。",
        "fr": "Protégez votre appareil des pièges matériels et des révisions incompatibles.",
        "es": "Protegiendo tu dispositivo de trampas silenciosas de hardware y revisiones rotas.",
        "de": "Schütze dein Gerät vor stillen Hardware-Fallen und inkompatiblen Revisionen.",
        "ja": "サイレントな基板改変による故障から、大切な機器を守ります。",
    },
    "support_story_1_body": {
        "en": "Innioasis regularly rolls out newer firmwares for players like the Y1 (targeting hidden board revisions like 6.0, 7.0, and 8.0) that silently break older devices. Worse, their website provides zero warnings and zero access to previous working firmwares to recover from a bricked state. Updater CE and the Community Firmware project pick up the pieces: we archive every legacy stock ROM, automatically verify your hardware revision, and keep your player running safely.",
        "zh-CN": "Innioasis 经常为 Y1 等播放器推出针对隐藏主板版本（如 6.0、7.0、8.0）的新固件，这些固件刷入较旧机器时可能导致异常甚至变砖。更糟糕的是，原厂官网既不提供硬件版本警示，也不提供历史旧固件供用户救砖回退。更新程序 与社区固件归档项目应运而生：我们归档了所有已知的原厂历史 ROM，自动校验硬件适配，让您的播放器长久安全运行。",
        "fr": "Innioasis publie régulièrement de nouveaux micrologiciels pour des modèles comme le Y1 (pour des révisions internes 6.0, 7.0, 8.0) qui rendent les anciens appareils inutilisables. Pire encore, aucun avertissement ni archive des anciennes versions ne figure sur leur site. Programme de mise à jour et l'archive communautaire prennent le relais : nous conservons chaque ROM officielle, vérifions votre matériel et assurons la pérennité de votre lecteur.",
        "es": "Innioasis lanza periódicamente firmwares para reproductores como el Y1 (para revisiones como 6.0, 7.0 u 8.0) que rompen los modelos anteriores. Peor aún, no ofrecen advertencias ni copias de firmwares anteriores para recuperar el equipo. Actualizador y el archivo comunitario rescatan tu dispositivo: archivamos cada ROM de fábrica, comprobamos tu hardware y mantenemos tu reproductor funcionando con seguridad.",
        "de": "Innioasis veröffentlicht regelmäßig neue Firmwares für Geräte wie den Y1 (für verdeckte Revisionen wie 6.0, 7.0, 8.0), die ältere Modelle unbrauchbar machen. Es gibt keinerlei Warnungen oder alte ROMs auf deren Website zum Wiederherstellen. Updater und das Community-Archiv fangen dies auf: Wir sichern alle Legacy-ROMs, prüfen Hardware-Revisionen automatisch und halten deinen Player am Leben.",
        "ja": "Innioasis は Y1 などのプレイヤーに対し、隠れた基板リビジョン（rev 6.0、7.0、8.0 など）向けの更新を告知なしで配信し、旧モデルで不具合や文鎮化を引き起こすことがあります。メーカー公式サイトには警告も過去のファームウェアも一切ありません。アップデーターとコミュニティアーカイブは、全バージョンの ROM を保存し、基板を自動判定して安全な復旧環境を提供しています。",
    },
    "support_story_1_item_1": {
        "en": "Rescuing Broken Players: Safety checks ensure newer updates never brick older hardware.",
        "zh-CN": "救砖与防患：多重安全检查，防止新版固件误刷损坏老批次硬件。",
        "fr": "Sauvetage des lecteurs : Vérifications pour ne jamais bloquer le matériel ancien.",
        "es": "Rescate de dispositivos: Comprobaciones para no inutilizar hardware antiguo.",
        "de": "Rettung älterer Player: Sicherheitschecks verhindern Defekte durch neue Firmwares.",
        "ja": "機器の救出と保護：安全チェックにより旧型番への誤書き込みを防止。",
    },
    "support_story_1_item_2": {
        "en": "Archived Stock ROMs: Complete collection of original factory firmwares Innioasis deleted.",
        "zh-CN": "原厂固件大归档：完整收录已被官方下架的所有出厂原版固件。",
        "fr": "ROMs d'origine archivées : Collection complète des micrologiciels d'usine supprimés.",
        "es": "ROMs de fábrica archivadas: Colección completa de firmwares originales retirados.",
        "de": "Archivierte Stock-ROMs: Lückenlose Sammlung aller vom Hersteller gelöschten ROMs.",
        "ja": "歴代ファームウェア保存：公式サイトから消えた初期純正 ROM を完全保管。",
    },
    "support_story_1_item_3": {
        "en": "Native Cross-Platform: Full flashing tools for macOS, Linux, and Windows alike.",
        "zh-CN": "原生跨平台支持：全面支持 macOS、Linux 与 Windows 原生刷机。",
        "fr": "Multiplateforme natif : Outils de flash complets pour macOS, Linux et Windows.",
        "es": "Multiplataforma nativo: Herramientas de flasheo para macOS, Linux y Windows.",
        "de": "Natives Cross-Platform: Vollständige Flash-Tools für macOS, Linux und Windows.",
        "ja": "マルチOS対応：macOS、Linux、Windows それぞれで快適に動作。",
    },
    "support_story_1_item_4": {
        "en": "Hardware Intelligence: Automatic board detection so you always get the right package.",
        "zh-CN": "主板智能识别：自动侦测硬件变体与批次，精准匹配对应版本。",
        "fr": "Détection intelligente : Identification automatique de la carte mère.",
        "es": "Detección inteligente: Reconocimiento automático de la placa base.",
        "de": "Intelligente Erkennung: Automatische Board-Erkennung für das richtige Paket.",
        "ja": "基板自動判定：内部基板を自動判別し、確実に適合するファームウェアを選択。",
    },
    "support_story_2_headline": {
        "en": "No broken Google Drives. No guess-work. Just working firmware.",
        "zh-CN": "告别失效网盘与死链，直连高速固件。",
        "fr": "Fini les Google Drive expirés. Des micrologiciels fiables et directs.",
        "es": "Sin enlaces caídos de Google Drive. Solo firmware funcional.",
        "de": "Keine toten Google-Drive-Links. Einfach funktionierende Firmware.",
        "ja": "期限切れの Google ドライブは不要。確実に動くファームウェアを。",
    },
    "support_story_2_lead": {
        "en": "High-speed, verified community infrastructure you can actually depend on.",
        "zh-CN": "由社区维护的高速稳定基础设施，随时值得信赖。",
        "fr": "Une infrastructure communautaire rapide et vérifiée sur laquelle vous pouvez compter.",
        "es": "Infraestructura comunitaria rápida y verificada en la que puedes confiar.",
        "de": "Schnelle, geprüfte Community-Infrastruktur, auf die Verlass ist.",
        "ja": "いつでも頼れる、高速で検証済みのコミュニティ配信ネットワーク。",
    },
    "support_story_2_body": {
        "en": "Tracking down official Innioasis software usually means scouring forums for expired Google Drive links, dead Baidu cloud shares, and mystery zip files with no flashing tools. We built a reliable, independent download network with verified SHA-256 integrity, update notifications, and step-by-step guided installs. Your donations directly keep the downloads and hosting that make this possible 24 hours a day, 365 days a year.",
        "zh-CN": "以往寻找 Innioasis 官方固件往往意味着在论坛里翻找失效的 Google Drive 链接、过期的百度网盘分享以及没有任何刷机教程的神秘压缩包。我们构建了高可用独立分发网络，具备 SHA-256 完整性校验、更新提醒与向导式刷机指引。您的捐赠将直接支持高速 CDN 流量与服务器运维，确保所有人全年无休享受极速下载。",
        "fr": "Trouver un logiciel officiel Innioasis implique souvent de chercher des liens Google Drive expirés et des archives douteuses. Nous avons créé un réseau de téléchargement indépendant et fiable avec vérification SHA-256, notifications de mise à jour et installation guidée. Vos dons financent directement la bande passante CDN et les serveurs pour maintenir ce service 24h/24.",
        "es": "Buscar software oficial de Innioasis suele significar toparse con enlaces caducados de Google Drive y archivos sospechosos. Hemos creado una red de descarga independiente con comprobación SHA-256, notificaciones de actualización e instalaciones guiadas. Tus donaciones cubren directamente el ancho de banda CDN y los servidores necesarios para que funcione todo el año.",
        "de": "Offizielle Innioasis-Firmwares zu finden bedeutete bisher, tote Google-Drive-Links und zweifelhafte ZIPs ohne Anleitung zu durchsuchen. Wir haben ein verlässliches Download-Netzwerk mit SHA-256-Prüfsummen, Update-Benachrichtigungen und geführten Schritt-für-Schritt-Installationen aufgebaut. Deine Spenden finanzieren direkt die Downloads und Server, die dies ermöglichen.",
        "ja": "メーカーの公式ファームウェアを探そうとすると、リンク切れの Google ドライブや怪しい掲示板に行き着きがちでした。私たちは SHA-256 検証済みで安心な高速 CDN 配信網、更新通知、親切な導入ガイドを整備しました。いただいた支援金は、24時間365日の高速ダウンロードとサーバー維持費に直接活用されています。",
    },
    "support_story_2_item_1": {
        "en": "Direct High-Speed CDN: Fast, direct downloads with no download limits or dead links.",
        "zh-CN": "高速直连 CDN：极速畅享下载，无配额限制，无失效死链。",
        "fr": "CDN rapide et direct : Téléchargements sans quotas ni liens morts.",
        "es": "CDN directo de alta velocidad: Descargas sin límites ni enlaces rotos.",
        "de": "Direktes High-Speed-CDN: Schnelle Downloads ohne Limits oder tote Links.",
        "ja": "高速ダイレクト CDN：制限なし・リンク切れなしの快適ダウンロード。",
    },
    "support_story_2_item_2": {
        "en": "Update Notifications: Get alerted the moment new community releases or fixes drop.",
        "zh-CN": "自动更新通知：当有新的社区固件或修复补丁发布时第一时间提示。",
        "fr": "Notifications de mise à jour : Alertes dès qu'un correctif ou une version sort.",
        "es": "Notificaciones de actualización: Avisos inmediatos ante nuevas versiones y parches.",
        "de": "Update-Benachrichtigungen: Sofortige Hinweise bei neuen Releases oder Fixes.",
        "ja": "アップデート通知：新しいファームウェアや修正が公開されたらすぐにお知らせ。",
    },
    "support_story_2_item_3": {
        "en": "Guided Step-by-Step Installs: Clear visual prompts walk you through every button press.",
        "zh-CN": "图形化引导式刷机：图文并茂，清晰指引每一步按键与操作。",
        "fr": "Installation guidée étape par étape : Instructions claires à chaque étape.",
        "es": "Instalación guiada paso a paso : Indicaciones visuales para cada botón y paso.",
        "de": "Geführte Schritt-für-Schritt-Installation: Klare visuelle Anleitungen für jeden Tastendruck.",
        "ja": "分かりやすいステップガイド：画面の指示通りに操作するだけで迷わず完了。",
    },
    "support_story_2_item_4": {
        "en": "Multilingual Community: Fully localised in English, Chinese, German, Spanish, French & Japanese.",
        "en-GB": "Multilingual Community: Fully localised in English, Chinese, German, Spanish, French & Japanese.",
        "en-US": "Multilingual Community: Fully localized in English, Chinese, German, Spanish, French & Japanese.",
        "zh-CN": "全方位多语言界面：完整本地化支持中、英、德、西、法、日等多国语言。",
        "fr": "Communauté multilingue : Entièrement traduit en anglais, chinois, allemand, espagnol, français et japonais.",
        "es": "Comunidad multilingüe: Totalmente traducido a inglés, chino, alemán, español, francés y japonés.",
        "de": "Mehrsprachige Community: Vollständig lokalisiert in Deutsch, Englisch, Chinesisch, Spanisch, Französisch und Japanisch.",
        "ja": "多言語完全対応：日本語、英語、中国語、ドイツ語、スペイン語、フランス語に対応。",
    },
    "support_story_3_headline": {
        "en": "Unlocking the full potential of your Innioasis player.",
        "zh-CN": "彻底释放 Innioasis 播放器的全部潜能。",
        "fr": "Libérez tout le potentiel de votre lecteur Innioasis.",
        "es": "Desbloquea todo el potencial de tu reproductor Innioasis.",
        "de": "Entfessle das volle Potenzial deines Innioasis-Players.",
        "ja": "Innioasis プレイヤーの真のポテンシャルを解放する。",
    },
    "support_story_3_lead": {
        "en": "Bringing Rockbox, custom lightweight operating systems, and audiophile freedom.",
        "zh-CN": "带来 Rockbox、轻量级定制系统与无拘无束的发烧音频体验。",
        "fr": "Intégrez Rockbox, des systèmes légers et une liberté audiophile totale.",
        "es": "Llevando Rockbox, sistemas operativos ligeros y libertad audiófila.",
        "de": "Mit Rockbox, schlanken Custom-Betriebssystemen und audiophiler Freiheit.",
        "ja": "Rockbox、軽量カスタム OS、そして妥協なき高音質再生環境をお届けします。",
    },
    "support_story_3_body": {
        "en": "Stock factory software only scratches the surface of what your Y1 or Y2 hardware can do. Our community brought Rockbox, lightweight custom operating systems, audiophile codec playback, and custom themes to life. We buy physical hardware variations out-of-pocket to test, reverse engineer, and verify every install. Supporting Updater CE directly funds these test units and accelerates custom firmware development.",
        "zh-CN": "原厂自带软件仅仅发挥了 Y1/Y2 硬件潜力的很小一部分。我们社区开发并移植了 Rockbox、轻量级定制操作系统、发烧级无损音频解码支持与丰富的主题。为了确保万无一失，开发者自费购买了各种硬件变体样机进行逆向和实机测试。您的支持能直接分担购机测试成本，加速更多固件与功能的开发。",
        "fr": "Le logiciel d'usine n'exploite qu'une fraction des capacités matérielles du Y1 ou Y2. Notre communauté a porté Rockbox, conçu des systèmes personnalisés légers et optimisé la lecture audiophile. Nous achetons nous-mêmes le matériel pour tester et vérifier chaque installation. Votre soutien finance ces appareils de test et accélère le développement.",
        "es": "El software de fábrica apenas aprovecha el hardware del Y1 o Y2. Nuestra comunidad ha hecho realidad Rockbox, sistemas personalizados ligeros, reproducción audiófila y temas exclusivos. Compramos de nuestro bolsillo diferentes variantes para probarlas. Tu apoyo financia estas unidades de prueba y acelera el desarrollo.",
        "de": "Die Werkssoftware nutzt das Potenzial der Y1- und Y2-Hardware kaum aus. Unsere Community hat Rockbox, schlanke Custom-Systeme, audiophile Codecs und Themes Wirklichkeit werden lassen. Wir kaufen Hardware-Varianten selbst, um jede Installation zu verifizieren. Deine Unterstützung finanziert Testgeräte und beschleunigt die Entwicklung.",
        "ja": "純正ソフトウェアは、Y1 や Y2 のハードウェア性能のごく一部しか引き出せていませんでした。私たちのコミュニティは Rockbox や軽量カスタム OS の移植、高音質コーデック対応、カスタムテーマを実現しました。開発者は自費で各リビジョンの実機を購入し入念に検証しています。皆様のご支援がテスト機材費となり、さらなる開発を後押しします。",
    },
    "support_story_3_item_1": {
        "en": "One-Click Rockbox: Audiophile playback and custom themes installed in seconds.",
        "zh-CN": "一键安装 Rockbox：秒级安装发烧级音频播放系统与精美主题。",
        "fr": "Rockbox en un clic : Lecture haute fidélité et thèmes installés en quelques secondes.",
        "es": "Rockbox en un clic: Reproducción de alta fidelidad y temas instalados al instante.",
        "de": "One-Click Rockbox: Audiophile Wiedergabe und Themes in Sekundenschnelle installiert.",
        "ja": "ワンクリックで Rockbox：高音質再生環境と多彩なテーマを一瞬でインストール。",
    },
    "support_story_3_item_2": {
        "en": "Community Firmware: Battery enhancements, cleaner UI, and modern audio codec support.",
        "zh-CN": "社区定制固件：续航优化、更清爽的操作界面以及现代无损音频支持。",
        "fr": "Firmware communautaire : Meilleure autonomie, interface épurée et codecs modernes.",
        "es": "Firmware comunitario: Mejor batería, interfaz limpia y soporte de códecs modernos.",
        "de": "Community-Firmware: Bessere Akkulaufzeit, saubere Oberfläche und moderne Codecs.",
        "ja": "コミュニティカスタムファームウェア：省電力化、洗練された UI、最新コーデック対応。",
    },
    "support_story_3_item_3": {
        "en": "Active Hardware Lab: We test on real physical devices before releasing any build.",
        "zh-CN": "实机测试实验室：每一款公开发布的固件均在真实机器上完整测试。",
        "fr": "Banc d'essai réel : Chaque version est vérifiée sur de vrais appareils physiques.",
        "es": "Laboratorio real: Probamos en dispositivos físicos antes de cualquier publicación.",
        "de": "Echtes Testlabor: Jedes Release wird auf physischen Geräten ausgiebig getestet.",
        "ja": "実機検証ラボ：すべてのビルドは、公開前に本物の実機で動作確認済み。",
    },
    "support_story_3_item_4": {
        "en": "Zero Bloatware: 100% free, privacy-first software with no ads or tracking.",
        "zh-CN": "纯净无捆绑：100% 免费、注重隐私，绝无广告或后台数据追踪。",
        "fr": "Zéro fioriture : 100% gratuit, respectueux de la vie privée, sans publicité.",
        "es": "Cero publicidad: 100% gratuito, centrado en la privacidad y sin rastreo.",
        "de": "Kein Ballast: 100% kostenlos, datenschutzfreundlich, werbefrei.",
        "ja": "完全無料・広告なし：広告やユーザー追跡のない、純粋で安全なオープンソース。",
    },
    "support_story_4_headline": {
        "en": "Help us do what Innioasis don't.",
        "zh-CN": "帮助我们做到原厂不愿做的事。",
        "fr": "Aidez-nous à faire ce qu'Innioasis ne fait pas.",
        "es": "Ayúdanos a hacer lo que Innioasis no hace.",
        "de": "Hilf uns zu tun, was Innioasis versäumt.",
        "ja": "メーカーが放棄した課題を、共に解決するために。",
    },
    "support_story_4_lead": {
        "en": "Community-powered firmware preservation keeping your devices out of landfills.",
        "zh-CN": "社区驱动的固件留存与维护计划，避免优质硬件过早沦为电子垃圾。",
        "fr": "La préservation communautaire qui évite à vos appareils de finir à la poubelle.",
        "es": "Preservación comunitaria para evitar que tus reproductores acaben en la basura.",
        "de": "Community-getriebene Erhaltung, die deine Geräte vor dem Elektroschrott bewahrt.",
        "ja": "愛用の音楽プレイヤーを電子ゴミにさせない、草の根の維持・保存活動。",
    },
    "support_story_4_body": {
        "en": "When a hardware company moves on to their next gadget, older models get abandoned. Innioasis provides no Linux support, no transparent changelogs, and no easy way to recover when an install fails. Updater CE exists because passionate users shouldn't have to throw away a perfectly good music player. 100% of community contributions go directly into keeping this tool open for everyone.",
        "zh-CN": "当硬件厂商转向新产品线时，老款设备往往被彻底遗忘。Innioasis 原厂不提供 Linux 支持、不公开详细变更日志，设备刷坏后也无简易救砖途径。更新程序 的存在就是为了让音乐爱好者不必丢弃性能完好的播放器。每一笔社区捐赠都 100% 用于维持这项服务，让它始终对所有人开放。",
        "fr": "Quand un fabricant passe au produit suivant, les anciens modèles sont délaissés. Innioasis n'offre aucun support Linux, pas de journal des modifications transparent ni de solution simple en cas de panne. Programme de mise à jour existe pour que personne n'ait à jeter un excellent lecteur. 100% des dons financent les serveurs et la gratuité pour tous.",
        "es": "Cuando una empresa pasa al siguiente producto, los modelos antiguos quedan abandonados. Innioasis no ofrece soporte en Linux, ni registros de cambios transparentes ni soluciones fáciles de recuperación. Actualizador existe para que no tires un reproductor perfectamente funcional. El 100% de las aportaciones financia servidores y mantiene todo libre.",
        "de": "Wenn Hersteller neue Produkte auf den Markt bringen, werden Vorgänger oft vergessen. Innioasis bietet keinen Linux-Support, keine transparenten Changelogs und keine einfache Rettung bei Fehlern. Updater existiert, damit du funktionierende Hardware nicht wegwerfen musst. 100% der Spenden fließen in den Betrieb des Projekts.",
        "ja": "メーカーが新型機へ移行すると、旧機種のサポートは打ち切られがちです。Innioasis は Linux をサポートせず、更新履歴も不透明で、書き込み失敗時の救済策もありません。まだ十分に使えるプレイヤーを無駄にしないためにアップデーターは生まれました。支援金は全額、インフラ費用とツールの無償提供維持に使われます。",
    },
    "support_story_4_item_1": {
        "en": "Rescue & Unbrick: Proven recovery flows even when devices refuse to boot.",
        "zh-CN": "救砖恢复支持：即便机器无法正常开机，也有经过验证的救砖流程。",
        "fr": "Sauvetage et débriquage : Procédures éprouvées même si l'appareil ne démarre plus.",
        "es": "Recuperación y desbriqueo: Flujos comprobados incluso si el dispositivo no enciende.",
        "de": "Rettung & Unbrick: Bewährte Abläufe, selbst wenn das Gerät nicht mehr startet.",
        "ja": "文鎮化救済：起動しなくなったプレイヤーでも確実に復旧できる手順を用意。",
    },
    "support_story_4_item_2": {
        "en": "Linux First-Class Support: Fully supported MediaTek flashing on Linux distros.",
        "zh-CN": "Linux 一等公民支持：在各类主流 Linux 发行版上提供完整的联发科刷机能力。",
        "fr": "Support Linux de premier plan : Flash MediaTek parfaitement pris en charge sous Linux.",
        "es": "Soporte Linux de primera : Flasheo MediaTek plenamente compatible en distribuciones Linux.",
        "de": "Linux-Support erster Klasse: Zuverlässiges MediaTek-Flashen auf gängigen Linux-Systemen.",
        "ja": "Linux 完全対応：各種 Linux ディストリビューションでも安定して MediaTek 書込が可能。",
    },
    "support_story_4_item_3": {
        "en": "Transparent Changelogs: Detailed release notes so you know exactly what changes.",
        "zh-CN": "透明更新日志：提供详细的发布说明，让您清楚知晓每一次固件改动的具体内容。",
        "fr": "Journaux transparents : Notes de version précises pour savoir ce qui change.",
        "es": "Registros transparentes: Notas detalladas para que sepas exactamente qué cambia.",
        "de": "Transparente Changelogs: Detaillierte Release-Notes für jeden Einblick in Änderungen.",
        "ja": "透明性の高い更新履歴：何がどう変更されたのか、分かりやすい変更点を明記。",
    },
    "support_story_4_item_4": {
        "en": "100% Community Funded: Every coffee keeps the archive open and free for all.",
        "zh-CN": "100% 社区资助：每一杯咖啡的赞助，都在帮助所有人维护这个免费归档库。",
        "fr": "100% financé par la communauté : Chaque café maintient l'archive ouverte et gratuite.",
        "es": "100% financado por la comunidad: Cada café mantiene el archivo accesible para todos.",
        "de": "100% von der Community getragen: Jeder Kaffee hält das Archiv für alle offen.",
        "ja": "100% コミュニティ支援：一杯のコーヒーが、世界中のユーザーへの無償提供を支えます。",
    },
    "support_methods_title": {
        "en": "Ways to Support",
        "zh-CN": "赞助与支持方式",
        "fr": "Moyens de soutien",
        "es": "Formas de apoyar",
        "de": "Unterstützungsmöglichkeiten",
        "ja": "ご支援方法",
    },
    "support_methods_subtitle": {
        "en": "Choose your preferred contribution method. Every donation directly funds downloads, hosting, and test hardware.",
        "zh-CN": "请选择您方便的支持方式。每一笔捐赠都将直接用于下载服务、托管及测试硬件采购。",
        "fr": "Choisissez votre méthode préférée. Chaque don finance directement la bande passante, l'hébergement et le matériel de test.",
        "es": "Elige tu método preferido. Cada donación financia el ancho de banda, el alojamiento y el hardware de pruebas.",
        "de": "Wähle deine bevorzugte Methode. Jede Spende fließt direkt in Downloads, Hosting und Test-Hardware.",
        "ja": "ご都合の良い方法をお選びください。いただいたご支援は、サーバー維持費、回線費用、テスト機材費に直接活用されます。",
    },
    "support_crypto_title": {
        "en": "Cryptocurrency (BTC, ETH, SHIB)",
        "zh-CN": "加密货币 (BTC, ETH, SHIB)",
        "fr": "Cryptomonnaies (BTC, ETH, SHIB)",
        "es": "Criptomonedas (BTC, ETH, SHIB)",
        "de": "Kryptowährungen (BTC, ETH, SHIB)",
        "ja": "暗号資産 (BTC, ETH, SHIB)",
    },
    "support_copy_address": {
        "en": "Copy",
        "zh-CN": "复制",
        "fr": "Copier",
        "es": "Copiar",
        "de": "Kopieren",
        "ja": "コピー",
    },
    "support_copied_btn": {
        "en": "Copied!",
        "zh-CN": "已复制！",
        "fr": "Copié !",
        "es": "¡Copiado!",
        "de": "Kopiert!",
        "ja": "コピー完了！",
    },

    # Update checking
    "update_available": {
        "zh-CN": "发现新版本",
        "en": "Update Available",
        "fr": "Mise à jour disponible",
        "es": "Actualización disponible",
    },
    "update_available_fmt": {
        "zh-CN": "发现新版本 <b>{new}</b>，您当前使用的是 <b>{current}</b>。",
        "en": "Version <b>{new}</b> is available — you are on <b>{current}</b>.",
        "fr": "La version <b>{new}</b> est disponible — vous êtes sur la <b>{current}</b>.",
        "es": "La versión <b>{new}</b> está disponible — tienes la <b>{current}</b>.",
    },
    "update_notes": {
        "zh-CN": "更新内容",
        "en": "What's new",
        "fr": "Nouveautés",
        "es": "Novedades",
    },
    "update_no_notes": {
        "zh-CN": "暂无更新说明。",
        "en": "No release notes provided.",
        "fr": "Aucune note de version fournie.",
        "es": "No se proporcionaron notas de la versión.",
    },
    "update_install_win": {
        "zh-CN": "下载 {hint} 并运行。安装程序会替换当前版本，您的设置与已下载的固件会保留。",
        "en": "Download the {hint} and run it. The installer replaces the current version; your settings and downloaded firmware are kept.",
        "fr": "Téléchargez le {hint} et exécutez-le. L'installateur remplace la version actuelle ; vos réglages et firmwares téléchargés sont conservés.",
        "es": "Descarga el {hint} y ejecútalo. El instalador reemplaza la versión actual; se conservan tus ajustes y firmwares descargados.",
    },
    "update_install_mac": {
        "zh-CN": "下载 {hint}，打开后把应用拖入“应用程序”文件夹。",
        "en": "Download the {hint}, open it, and drag the app into your Applications folder.",
        "fr": "Téléchargez le {hint}, ouvrez-le et glissez l'application dans le dossier Applications.",
        "es": "Descarga el {hint}, ábrelo y arrastra la app a tu carpeta de Aplicaciones.",
    },
    "update_install_linux": {
        "zh-CN": "下载 {hint}，赋予执行权限并运行——它会替换旧版本。",
        "en": "Download the {hint}, make it executable, and run it — it replaces the previous version.",
        "fr": "Téléchargez le {hint}, rendez-le exécutable et lancez-le — il remplace la version précédente.",
        "es": "Descarga el {hint}, hazlo ejecutable y ejecútalo: reemplaza la versión anterior.",
    },
    "update_install_generic": {
        "zh-CN": "从发布页面下载 {hint}，并按您系统的安装说明操作。",
        "en": "Download the {hint} from the release page and follow the installation instructions for your system.",
        "fr": "Téléchargez le {hint} depuis la page de version et suivez les instructions d'installation de votre système.",
        "es": "Descarga el {hint} desde la página de la versión y sigue las instrucciones de instalación de tu sistema.",
    },
    "update_btn_download": {
        "zh-CN": "下载更新",
        "en": "Download Update",
        "fr": "Télécharger la mise à jour",
        "es": "Descargar actualización",
    },
    "update_btn_later": {
        "zh-CN": "稍后提醒",
        "en": "Remind Me Later",
        "fr": "Plus tard",
        "es": "Recordármelo más tarde",
    },
    "update_btn_skip": {
        "zh-CN": "跳过此版本",
        "en": "Skip This Version",
        "fr": "Ignorer cette version",
        "es": "Omitir esta versión",
    },
    "update_up_to_date": {
        "zh-CN": "您已是最新版本 <b>{version}</b>。",
        "en": "You're up to date — version <b>{version}</b> is the latest.",
        "fr": "Vous êtes à jour — la version <b>{version}</b> est la dernière.",
        "es": "Estás al día: la versión <b>{version}</b> es la más reciente.",
    },
    "update_check_failed": {
        "zh-CN": "暂时无法检查更新。请检查网络连接后重试。",
        "en": "Couldn't check for updates right now. Check your connection and try again.",
        "fr": "Impossible de vérifier les mises à jour pour le moment. Vérifiez votre connexion et réessayez.",
        "es": "No se pudo comprobar si hay actualizaciones ahora. Revisa tu conexión e inténtalo de nuevo.",
    },

    # Settings & Release Reminders
    "settings_title": {
        "zh-CN": "设置与偏好",
        "en": "Settings & Preferences",
        "fr": "Paramètres et préférences",
        "es": "Ajustes y preferencias",
    },
    "settings_reminders_group": {
        "zh-CN": "播放器更新提醒",
        "en": "Player Update Reminders",
        "fr": "Rappels de mise à jour du lecteur",
        "es": "Recordatorios de actualización del reproductor",
        "de": "Update-Erinnerungen für Player",
        "ja": "プレーヤーのアップデート通知",
    },
    "settings_reminders_desc": {
        "zh-CN": "当播放器有新软件版本发布时，我们会通知您。",
        "en": "We'll let you know when a new version of the software for your player is released.",
        "fr": "Nous vous informerons lorsqu'une nouvelle version du logiciel de votre lecteur sera publiée.",
        "es": "Te avisaremos cuando se publique una nueva versión del software de tu reproductor.",
    },
    "settings_enable_y1": {
        "zh-CN": "启用 Innioasis Y1 固件更新提醒",
        "en": "Enable release reminders for Innioasis Y1",
        "fr": "Activer les rappels pour Innioasis Y1",
        "es": "Activar recordatorios para Innioasis Y1",
    },
    "settings_enable_y2": {
        "zh-CN": "启用 Innioasis Y2 固件更新提醒",
        "en": "Enable release reminders for Innioasis Y2",
        "fr": "Activer les rappels pour Innioasis Y2",
        "es": "Activar recordatorios para Innioasis Y2",
    },
    "settings_enable_a5": {
        "zh-CN": "启用 Innioasis A5 固件更新提醒",
        "en": "Enable release reminders for Innioasis A5",
        "fr": "Activer les rappels pour Innioasis A5",
        "es": "Activar recordatorios para Innioasis A5",
    },
    "settings_installed_status": {
        "zh-CN": "上次安装: <b>{software}</b> ({version}) · {date}",
        "en": "Last installed: <b>{software}</b> ({version}) · {date}",
        "fr": "Dernière installation : <b>{software}</b> ({version}) · {date}",
        "es": "Última instalación: <b>{software}</b> ({version}) · {date}",
    },
    "settings_not_installed": {
        "zh-CN": "尚未记录安装历史（完成一次刷机后将自动记录）",
        "en": "No installation recorded yet (automatically saved after flashing)",
        "fr": "Aucune installation enregistrée (enregistrée automatiquement après le flash)",
        "es": "Ninguna instalación registrada todavía (se guarda automáticamente tras flashear)",
    },
    "settings_clear_install": {
        "zh-CN": "清除记录",
        "en": "Clear Record",
        "fr": "Effacer l'enregistrement",
        "es": "Borrar registro",
    },
    "settings_check_firmware_updates": {
        "zh-CN": "立即检查设备固件更新",
        "en": "Check for Device Updates Now",
        "fr": "Vérifier les mises à jour des appareils",
        "es": "Buscar actualizaciones de dispositivos ahora",
    },
    "settings_firmware_up_to_date": {
        "zh-CN": "已记录的播放器均已运行最新固件版本！",
        "en": "All tracked devices are running the latest firmware releases!",
        "fr": "Tous les appareils suivis utilisent les dernières versions de firmware !",
        "es": "¡Todos los dispositivos registrados están ejecutando las últimas versiones de firmware!",
    },
    "settings_no_devices_tracked": {
        "zh-CN": "尚未记录任何设备的安装历史。请先完成一次固件安装。",
        "en": "No device installations have been recorded yet. Flash a device to enable version tracking.",
        "fr": "Aucune installation d'appareil n'a encore été enregistrée. Flashez un appareil pour activer le suivi.",
        "es": "Aún no se ha registrado ninguna instalación de dispositivo. Flashee un dispositivo para habilitar el seguimiento.",
    },
    "settings_donations_group": {
        "zh-CN": "捐赠、致谢与社区",
        "en": "Donations, Credits and Community",
        "fr": "Dons, crédits et communauté",
        "es": "Donaciones, créditos y comunidad",
        "de": "Spenden, Credits und Community",
        "ja": "寄付・クレジット・コミュニティ",
    },
    # The switch on that card's own title line: on = credits and donation
    # prompts are enabled by default.
    "settings_show_donations": {
        "zh-CN": "捐赠、致谢与社区",
        "en": "Donations, Credits and Community",
        "fr": "Dons, crédits et communauté",
        "es": "Donaciones, créditos y comunidad",
        "de": "Spenden, Credits und Community",
        "ja": "寄付・クレジット・コミュニティ",
    },
    "settings_hide_donations": {
        "zh-CN": "关闭捐赠 / 致谢",
        "en": "Turn off Donations / Credits",
        "fr": "Désactiver les dons / crédits",
        "es": "Desactivar donaciones / créditos",
        "de": "Spenden / Credits ausschalten",
        "ja": "寄付 / クレジットをオフにする",
    },
    "settings_hide_donations_tip": {
        "zh-CN": "显示社区致谢和捐赠提示。关闭即可禁用；你仍可在设置中随时请喝咖啡。",
        "en": "Shows community credits and donation prompts. Turn off to disable them — you can still buy a coffee from Settings any time.",
        "fr": "Affiche les crédits communautaires et les invites de don. Désactivez pour les masquer — vous pouvez toujours offrir un café depuis les paramètres.",
        "es": "Muestra créditos comunitarios y solicitudes de donación. Desactívalo para inhabilitarlos: aún puedes invitar a un café desde Ajustes.",
        "de": "Zeigt Community-Credits und Spendenhinweise. Ausschalten deaktiviert sie — Sie können jederzeit in den Einstellungen einen Kaffee spendieren.",
        "ja": "コミュニティクレジットと寄付の案内を表示します。オフにすると無効になります（設定からいつでもコーヒーをご馳走できます）。",
    },
    "settings_donations_dismiss": {
        "zh-CN": "以后不再显示",
        "en": "Never show me this again",
        "fr": "Ne plus me montrer ceci",
        "es": "No volver a mostrarme esto",
        "de": "Nie wieder anzeigen",
        "ja": "もう表示しない",
    },
    "settings_skip_install_donations": {
        "zh-CN": "刷机成功后跳过捐赠提示",
        "en": "Skip post-installation donation prompts",
        "fr": "Ignorer les invites de dons après l'installation",
        "es": "Omitir los avisos de donación tras la instalación",
    },
    "settings_skip_install_donations_tip": {
        "zh-CN": "刷机完成后直接显示标准完成说明，不弹出求助捐赠窗口。",
        "en": "Show simple completion details without requesting donations after flashing.",
        "fr": "Affiche les détails d'achèvement sans demander de dons après le flash.",
        "es": "Muestra detalles simples de finalización sin solicitar donaciones tras el flasheo.",
    },
    "settings_cache_group": {
        "zh-CN": "固件缓存管理",
        "en": "Firmware Storage & Cache",
        "fr": "Stockage et cache du micrologiciel",
        "es": "Almacenamiento y caché de firmware",
        "de": "Firmware-Speicher & Cache",
        "ja": "ファームウェアストレージとキャッシュ",
    },
    "settings_cache_desc": {
        "zh-CN": "清理已下载的固件安装包和解压缓存以释放磁盘空间。此操作不会清除播放器固件安装记录或更新提醒偏好。",
        "en": "Delete downloaded firmware packages and extracted ROM files to free up disk space. Your install history and update reminder preferences are preserved.",
        "fr": "Supprimez les micrologiciels téléchargés et les fichiers ROM extraits pour libérer de l'espace disque. Vos préférences de rappel et votre historique sont conservés.",
        "es": "Elimine los paquetes de firmware descargados y los archivos ROM extraídos para liberar espacio. Su historial de instalación y preferencias de recordatorios se conservan.",
        "de": "Heruntergeladene Firmware-Pakete und extrahierte ROM-Dateien löschen, um Speicherplatz freizugeben. Ihr Installationsverlauf und Ihre Erinnerungen bleiben erhalten.",
        "ja": "ダウンロードしたファームウェアと展開済みROMファイルを削除してディスク容量を解放します。インストール履歴と通知設定は保持されます。",
    },
    "settings_clear_cache_btn": {
        "zh-CN": "清理固件缓存",
        "en": "Clear Firmware Cache",
        "fr": "Vider le cache du micrologiciel",
        "es": "Limpiar caché de firmware",
        "de": "Firmware-Cache leeren",
        "ja": "ファームウェアキャッシュをクリア",
    },
    "settings_cache_cleared_msg": {
        "zh-CN": "已清理 {count} 个文件/目录（释放了 {size} 磁盘空间）。",
        "en": "Cleared {count} item(s) ({size} freed).",
        "fr": "{count} élément(s) supprimé(s) ({size} libéré(s)).",
        "es": "Se eliminaron {count} elemento(s) ({size} liberado(s)).",
        "de": "{count} Element(e) bereinigt ({size} freigegeben).",
        "ja": "{count} 個の項目をクリアしました ({size} を解放)。",
    },

    # Reminder Dialog
    "reminder_new_release_title": {
        "zh-CN": "发现新固件版本",
        "en": "New Firmware Release Available",
        "fr": "Nouvelle version de firmware disponible",
        "es": "Nueva versión de firmware disponible",
    },
    "reminder_new_release_msg": {
        "zh-CN": "你的 <b>{device}</b> 有 <b>{software}</b> 的更新可用。",
        "en": "There's an update to <b>{software}</b> available for your <b>{device}</b>.",
        "fr": "Une mise à jour de <b>{software}</b> est disponible pour votre <b>{device}</b>.",
        "es": "Hay una actualización de <b>{software}</b> disponible para tu <b>{device}</b>.",
        "de": "Für dein <b>{device}</b> ist ein Update für <b>{software}</b> verfügbar.",
        "ja": "お使いの <b>{device}</b> 向けに <b>{software}</b> のアップデートがあります。",
    },
    "reminder_installed_version": {
        "zh-CN": "当前安装版本",
        "en": "Currently installed:",
        "fr": "Actuellement installé :",
        "es": "Actualmente instalado:",
    },
    "reminder_latest_version": {
        "zh-CN": "最新可用版本",
        "en": "New release available:",
        "fr": "Nouvelle version disponible :",
        "es": "Nueva versión disponible:",
    },
    "reminder_view_release": {
        "zh-CN": "前往查看 / 刷机",
        "en": "View Release / Update",
        "fr": "Voir la version / Mettre à jour",
        "es": "Ver versión / Actualizar",
    },
    "reminder_dismiss": {
        "zh-CN": "稍后再说",
        "en": "Later",
        "fr": "Plus tard",
        "es": "Más tarde",
    },
    "reminder_dont_remind_device": {
        "zh-CN": "不再提醒此设备的新固件",
        "en": "Don't remind me about new releases for this device",
        "fr": "Ne plus me rappeler les nouvelles versions pour cet appareil",
        "es": "No volver a recordarme nuevas versiones para este dispositivo",
    },
    "reminder_dont_remind_release": {
        "zh-CN": "不再提醒此版本",
        "en": "Do not remind me about this release",
        "fr": "Ne plus me rappeler pour cette version",
        "es": "No recordarme sobre esta versión",
    },
    "reminder_prompt_install": {
        "zh-CN": "是否使用您当前选择的方式（{method}）立即开始安装？",
        "en": "Would you like to start an install for it (with your selected method: {method})?",
        "fr": "Souhaitez-vous lancer l'installation (avec votre méthode sélectionnée : {method}) ?",
        "es": "¿Desea iniciar la instalación (con su método seleccionado: {method})?",
    },
    "reminder_prompt_install_generic": {
        "zh-CN": "是否立即开始安装？",
        "en": "Would you like to start an install for it?",
        "fr": "Souhaitez-vous lancer l'installation ?",
        "es": "¿Desea iniciar la instalación?",
    },
    "reminder_start_install": {
        "zh-CN": "开始安装",
        "en": "Start Install",
        "fr": "Démarrer l'installation",
        "es": "Iniciar instalación",
    },

    # Retry guidance — the player must be reset by hand before another attempt
    "retry_guidance_title": {
        "zh-CN": "请重置播放器",
        "en": "Reset Your Player",
        "fr": "Réinitialisez votre lecteur",
        "es": "Reinicia tu reproductor",
    },
    "retry_guidance_intro": {
        "zh-CN": "与播放器的 USB 连接已停止响应。再次尝试之前，需要先手动重置播放器。",
        "en": "The USB connection to your player has stopped responding. Your player needs a manual reset before another attempt can work.",
        "fr": "La connexion USB avec votre lecteur ne répond plus. Une réinitialisation manuelle est nécessaire avant toute nouvelle tentative.",
        "es": "La conexión USB con tu reproductor ha dejado de responder. Tu reproductor necesita un reinicio manual antes de poder volver a intentarlo.",
    },
    "retry_guidance_step_1": {
        "zh-CN": "1. 从播放器上拔下 USB 线。",
        "en": "1. Remove the USB cable from your player.",
        "fr": "1. Débranchez le câble USB de votre lecteur.",
        "es": "1. Desconecta el cable USB de tu reproductor.",
    },
    "retry_guidance_step_2": {
        "zh-CN": "2. 用卡针、回形针或耳钉按下隐藏的复位按钮。",
        "en": "2. Press the hidden reset button with a pin, paperclip or earring.",
        "fr": "2. Appuyez sur le bouton de réinitialisation caché avec une épingle, un trombone ou une boucle d'oreille.",
        "es": "2. Pulsa el botón de reinicio oculto con un alfiler, un clip o un pendiente.",
    },
    "retry_guidance_step_3": {
        "zh-CN": "3. 重新插上 USB 线，然后再次尝试。",
        "en": "3. Plug the USB cable back in, then try again.",
        "fr": "3. Rebranchez le câble USB, puis réessayez.",
        "es": "3. Vuelve a conectar el cable USB y reinténtalo.",
    },
    "retry_guidance_detail_errno": {
        "zh-CN": "刷机工具报告：{code}",
        "en": "The flasher reported: {code}",
        "fr": "Le flasheur a signalé : {code}",
        "es": "El flasheador informó: {code}",
    },
    "retry_guidance_detail_failed": {
        "zh-CN": "刷机失败原因：{code}",
        "en": "The flash failed with: {code}",
        "fr": "Le flashing a échoué : {code}",
        "es": "El flashing falló: {code}",
    },
    "retry_guidance_btn_retry": {
        "zh-CN": "重试",
        "en": "Try Again",
        "fr": "Réessayer",
        "es": "Reintentar",
    },

    # Settings — single release-reminder opt-in
    "settings_reminders_enable": {
        "zh-CN": "有新更新时通知我",
        "en": "Tell me about new updates to my player",
        "fr": "Informez-moi des nouvelles mises à jour de mon lecteur",
        "es": "Avísame de las nuevas actualizaciones de mi reproductor",
    },
    "settings_reminders_tracked": {
        "zh-CN": "当 {devices} 上的 {software} 有更新时，我们会通知您。",
        "en": "We'll let you know when there's an update to {software} on your {devices}.",
        "fr": "Nous vous informerons lorsqu'une mise à jour de {software} sera disponible sur votre {devices}.",
        "es": "Te avisaremos cuando haya una actualización de {software} en tu {devices}.",
    },
    # Reminder copy reads from the software actually tracked. Original Software
    # is the player's own firmware, so naming it twice reads worse than "the
    # software"; another launcher (Rockbox, Innioasis Music...) is named.
    "settings_reminders_tracked_original": {
        "zh-CN": "当您的 {model} 有新版本软件发布时，我们会通知您。",
        "en": "We'll let you know when a new version of the software for your {model} is released.",
        "fr": "Nous vous préviendrons lorsqu'une nouvelle version du logiciel de votre {model} sera disponible.",
        "es": "Te avisaremos cuando se publique una nueva versión del software de tu {model}.",
        "de": "Wir sagen dir Bescheid, wenn eine neue Version der Software für deinen {model} erscheint.",
        "ja": "{model} 用ソフトウェアの新しいバージョンが公開されたらお知らせします。",
    },
    "settings_reminders_tracked_named": {
        "zh-CN": "当 {software} 为您的 {model} 发布新版本时，我们会通知您。",
        "en": "We'll let you know when a new version of {software} is released for your {model}.",
        "fr": "Nous vous préviendrons lorsqu'une nouvelle version de {software} sortira pour votre {model}.",
        "es": "Te avisaremos cuando se publique una nueva versión de {software} para tu {model}.",
        "de": "Wir sagen dir Bescheid, wenn eine neue Version von {software} für deinen {model} erscheint.",
        "ja": "{model} 用の {software} の新しいバージョンが公開されたらお知らせします。",
    },
    "settings_reminders_tracked_multi": {
        "zh-CN": "以下项目有新版本时，我们会通知您。",
        "en": "We'll let you know when new versions of these are released.",
        "fr": "Nous vous préviendrons lorsque de nouvelles versions de ces logiciels seront publiées.",
        "es": "Te avisaremos cuando se publiquen nuevas versiones de estos.",
        "de": "Wir sagen dir Bescheid, wenn neue Versionen davon erscheinen.",
        "ja": "これらの新しいバージョンが公開されたらお知らせします。",
    },
    # One reminder switch per model that has software tracked on it.
    "settings_reminders_model_fmt": {
        "zh-CN": "{software}（{model}）",
        "en": "{software} on {model}",
        "fr": "{software} sur {model}",
        "es": "{software} en {model}",
        "de": "{software} auf {model}",
        "ja": "{model} の {software}",
    },

    # Settings — install method
    "settings_method_group": {
        "zh-CN": "安装方式",
        "en": "Install Method",
        "fr": "Méthode d'installation",
        "es": "Método de instalación",
    },
    "settings_method_desc": {
        "zh-CN": "固件写入播放器的方式。Windows 与 Linux 推荐使用 SP Flash Tool 控制台 XML 模式；macOS 仅支持 MTKClient。",
        "en": "SP Flash Tool is the default on Linux. MTKClient is the default on macOS. You can switch manually here.",
        "fr": "Méthode d'écriture du firmware sur votre lecteur. Sous Windows et Linux, le mode console XML de SP Flash Tool est recommandé ; macOS utilise MTKClient.",
        "es": "Cómo se escribe el firmware en tu reproductor. En Windows y Linux se recomienda el modo consola XML de SP Flash Tool; macOS usa MTKClient.",
    },
    "flash_method_mtk_mac": {
        "zh-CN": "MTKClient (Mac)",
        "en": "MTKClient (Mac)",
        "fr": "MTKClient (Mac)",
        "es": "MTKClient (Mac)",
    },
    "flash_method_note_mtk_mac": {
        "zh-CN": "模拟 macOS 模式：在本机运行 Mac 流程——仅使用 MTKClient，并显示 macOS 专属提示。仅供测试；应用将重启以生效。",
        "en": "Simulated macOS mode: runs the Mac flow on this machine — MTKClient is the only backend and macOS-specific prompts are shown. For testing only; the app restarts to apply.",
        "fr": "Mode macOS simulé : exécute le flux Mac sur cette machine — MTKClient est le seul moteur et les invites macOS sont affichées. Pour les tests uniquement ; l'application redémarre pour appliquer.",
        "es": "Modo macOS simulado: ejecuta el flujo de Mac en esta máquina — MTKClient es el único motor y se muestran los avisos de macOS. Solo para pruebas; la aplicación se reinicia para aplicarlo.",
    },
    "simulated_mac_title": {
        "zh-CN": "切换到模拟 macOS 模式",
        "en": "Switch to Simulated macOS Mode",
        "fr": "Passer en mode macOS simulé",
        "es": "Cambiar a modo macOS simulado",
    },
    "simulated_mac_body_mediatek": {
        "zh-CN": "MTKClient 将成为唯一的安装方式，并显示 macOS 专属提示。MediaTek 安装工具需要重启才能应用。",
        "en": "MTKClient becomes the only install method and macOS-specific prompts are shown. MediaTek Installer needs to restart to apply this.",
        "fr": "MTKClient devient la seule méthode d'installation et les invites macOS sont affichées. L'Installateur MediaTek doit redémarrer pour appliquer ce réglage.",
        "es": "MTKClient pasa a ser el único método de instalación y se muestran los avisos de macOS. El Instalador MediaTek debe reiniciarse para aplicarlo.",
    },
    "simulated_mac_body": {
        "zh-CN": "MTKClient 将成为唯一的安装方式，并显示 macOS 专属提示。更新程序 需要重启才能应用。",
        "en": "MTKClient becomes the only install method and macOS-specific prompts are shown. Updater CE needs to restart to apply this.",
        "fr": "MTKClient devient la seule méthode d'installation et les invites macOS sont affichées. Programme de mise à jour doit redémarrer pour appliquer ce réglage.",
        "es": "MTKClient pasa a ser el único método de instalación y se muestran los avisos de macOS. Actualizador debe reiniciarse para aplicarlo.",
    },
    "simulated_mac_restart_now": {
        "zh-CN": "立即重启",
        "en": "Restart Now",
        "fr": "Redémarrer maintenant",
        "es": "Reiniciar ahora",
    },
    "simulated_mac_later": {
        "zh-CN": "稍后",
        "en": "Later",
        "fr": "Plus tard",
        "es": "Más tarde",
    },
    "sel_available_software": {
        "zh-CN": "可用系统软件",
        "en": "Available System Software",
        "fr": "Logiciels système disponibles",
        "es": "Software de sistema disponible",
    },
    "sel_status": {
        "zh-CN": "状态",
        "en": "Status",
        "fr": "Statut",
        "es": "Estado",
    },
    "sel_ready": {
        "zh-CN": "就绪",
        "en": "Ready",
        "fr": "Prêt",
        "es": "Listo",
    },
    "sel_about_update": {
        "zh-CN": "关于此更新：",
        "en": "About this update:",
        "fr": "À propos de cette mise à jour :",
        "es": "Acerca de esta actualización:",
    },
    "sel_instructions_below": {
        "zh-CN": "请按照以下说明在您的 {lbl} 上安装此软件",
        "en": "Please follow the instructions below to install this software on your {lbl}",
        "fr": "Veuillez suivre les instructions ci-dessous pour installer ce logiciel sur votre {lbl}",
        "es": "Siga las instrucciones a continuación para instalar este software en su {lbl}",
    },
    "sel_install_tooltip": {
        "zh-CN": "安装或恢复选定的系统软件到您的 {lbl}",
        "en": "Install or restore the selected system software to your {lbl}",
        "fr": "Installer ou restaurer le logiciel système sélectionné sur votre {lbl}",
        "es": "Instalar o restaurar el software de sistema seleccionado en su {lbl}",
    },
    "sel_choose_firmware_pkg": {
        "zh-CN": "选择固件包",
        "en": "Choose Firmware Package",
        "fr": "Choisir le paquet de micrologiciel",
        "es": "Elegir paquete de firmware",
    },
    # MediaTek Installer: the three files SP Flash Tool's own setup asks for.
    "mtk_choose_flash_files": {
        "zh-CN": "选择刷机文件",
        "en": "Choose Flash Files",
        "fr": "Choisir les fichiers de flash",
        "es": "Elegir archivos de flasheo",
    },
    "mtk_da_label": {
        "zh-CN": "下载代理 (DA)",
        "en": "Download Agent",
        "fr": "Agent de téléchargement",
        "es": "Agente de descarga",
    },
    "mtk_da_placeholder": {
        "zh-CN": "请选择下载代理文件 (.bin)…",
        "en": "Select the Download Agent (.bin)…",
        "fr": "Sélectionnez l'agent de téléchargement (.bin)…",
        "es": "Selecciona el agente de descarga (.bin)…",
    },
    "mtk_da_filter": {
        "zh-CN": "下载代理 (*.bin);;所有文件 (*.*)",
        "en": "Download Agent (*.bin);;All Files (*.*)",
        "fr": "Agent de téléchargement (*.bin);;Tous les fichiers (*.*)",
        "es": "Agente de descarga (*.bin);;Todos los archivos (*.*)",
    },
    "mtk_da_required": {
        "zh-CN": "请先选择下载代理 (DA) 再开始安装。",
        "en": "Choose a Download Agent before starting the install.",
        "fr": "Choisissez un agent de téléchargement avant de lancer l'installation.",
        "es": "Elige un agente de descarga antes de iniciar la instalación.",
    },
    "mtk_scatter_label": {
        "zh-CN": "分区表文件 (scatter)",
        "en": "Scatter File",
        "fr": "Fichier scatter",
        "es": "Archivo scatter",
    },
    "mtk_scatter_placeholder": {
        "zh-CN": "请选择 scatter 文件 (.txt)…",
        "en": "Select the scatter file (.txt)…",
        "fr": "Sélectionnez le fichier scatter (.txt)…",
        "es": "Selecciona el archivo scatter (.txt)…",
    },
    "mtk_scatter_filter": {
        "zh-CN": "Scatter 文件 (*.txt);;所有文件 (*.*)",
        "en": "Scatter Files (*.txt);;All Files (*.*)",
        "fr": "Fichiers scatter (*.txt);;Tous les fichiers (*.*)",
        "es": "Archivos scatter (*.txt);;Todos los archivos (*.*)",
    },
    "sel_type_help_btn": {
        "zh-CN": "类型？",
        "en": "Type?",
        "fr": "Type ?",
        "es": "¿Tipo?",
    },
    "sel_type_help_tooltip": {
        "zh-CN": "请先尝试使用 Type A 系统软件。如果安装后滚轮没有反应，请安装 Type B 选项之一。B 型设备是指出厂时搭载 2.0 之前固件版本的设备。",
        "en": "Try Type A System Software first. If your scroll wheel doesn't respond after installation, install one of the Type B options. Type B devices are those that came with firmware versions earlier than 2.0.",
        "fr": "Essayez d'abord le logiciel système de type A. Si votre molette ne répond pas après l'installation, installez l'une des options de type B. Les appareils de type B sont ceux livrés avec une version de firmware antérieure à 2.0.",
        "es": "Pruebe primero el software de sistema Tipo A. Si la rueda táctil no responde después de la instalación, instale una de las opciones de Tipo B. Los dispositivos Tipo B son los que se entregaron con versiones de firmware anteriores a 2.0.",
    },
    "sel_refresh_tooltip": {
        "zh-CN": "刷新可用软件目录",
        "en": "Refresh available software catalogue",
        "en-US": "Refresh available software catalog",
        "fr": "Actualiser le catalogue des logiciels disponibles",
        "es": "Actualizar el catálogo de software disponible",
    },
    "sel_releases_count_fmt": {
        "zh-CN": "{count} 个版本 — 安装",
        "en": "{count} release(s) — Install",
        "fr": "{count} version(s) — Installer",
        "es": "{count} versión(es) — Instalar",
    },
    "device_type_help_title": {
        "zh-CN": "设备类型帮助",
        "en": "Device Type Help",
        "fr": "Aide sur le type d'appareil",
        "es": "Ayuda sobre el tipo de dispositivo",
    },
    "device_type_help_msg": {
        "zh-CN": "请先尝试使用 Type A 系统软件。\n\n如果安装后滚轮没有反应，请安装 Type B 选项之一。\n\nB 型设备是指出厂时搭载 2.0 之前固件版本的设备。",
        "en": "Try Type A System Software first.\n\nIf your scroll wheel doesn't respond after installation, install one of the Type B options.\n\nType B devices are those that came with firmware versions earlier than 2.0.",
        "fr": "Essayez d'abord le logiciel système de type A.\n\nSi votre molette ne répond pas après l'installation, installez l'une des options de type B.\n\nLes appareils de type B sont ceux livrés avec une version de firmware antérieure à 2.0.",
        "es": "Pruebe primero el software de sistema Tipo A.\n\nSi la rueda táctil no responde después de la instalación, instale una de las opciones de Tipo B.\n\nLos dispositivos Tipo B son los que se entregaron con versiones de firmware anteriores a 2.0.",
    },
    "software_install_instructions_title": {
        "zh-CN": "软件安装说明",
        "en": "Software Install Instructions",
        "fr": "Instructions d'installation du logiciel",
        "es": "Instrucciones de instalación del software",
    },
    "button_centre": {
        "zh-CN": "中间按钮",
        "en": "centre button",
        "en-US": "center button",
        "fr": "bouton central",
        "es": "botón central",
    },
    "button_power_lock": {
        "zh-CN": "电源/锁屏按钮",
        "en": "power/lock button",
        "fr": "bouton marche/arrêt/verrouillage",
        "es": "botón de encendido/bloqueo",
    },
    "button_power": {
        "zh-CN": "电源按钮",
        "en": "power button",
        "fr": "bouton marche/arrêt",
        "es": "botón de encendido",
    },
    "install_power_on_steps_fmt": {
        "zh-CN": "拔下您的 {label}，然后按住{button}直到开机。",
        "en": "Unplug your {label}, then hold the {button} until it turns on.",
        "fr": "Débranchez votre {label}, puis maintenez le {button} jusqu'à ce qu'il s'allume.",
        "es": "Desconecta tu {label}, luego mantén presionado el {button} hasta que se encienda.",
    },
    "install_disconnect_guidance_fmt": {
        "zh-CN": "在继续之前，请：\n\n1. 如果设备尚未关机，请关闭您的 {label}\n   （如有需要，可以使用大头针或回形针按重置键）\n\n2. 如果已连接，请从您的 {label} 断开 USB 线缆\n\n然后点击“确定”并按照屏幕上的说明进行操作。",
        "en": "Before continuing, please:\n\n1. If it isn't already off, power off your {label}\n   (You can use a pin or paperclip to press the reset button if needed)\n\n2. If it is connected, disconnect the USB cable from your {label}\n\nThen click OK and follow the on-screen instructions.",
        "fr": "Avant de continuer, veuillez :\n\n1. Si l'appareil n'est pas éteint, éteignez votre {label}\n   (Vous pouvez utiliser un trombone pour appuyer sur le bouton de réinitialisation si nécessaire)\n\n2. S'il est connecté, débranchez le câble USB de votre {label}\n\nCliquez ensuite sur OK et suivez les instructions à l'écran.",
        "es": "Antes de continuar, por favor:\n\n1. Si aún no está apagado, apaga tu {label}\n   (Puedes usar un clip o alfiler para presionar el botón de reinicio si es necesario)\n\n2. Si está conectado, desconecta el cable USB de tu {label}\n\nLuego haz clic en Aceptar y sigue las instrucciones en pantalla.",
    },
    "install_success_status_fmt": {
        "zh-CN": "{software} 安装成功。{steps}",
        "en": "{software} installed successfully. {steps}",
        "fr": "{software} installé avec succès. {steps}",
        "es": "{software} instalado con éxito. {steps}",
    },
    "donate_installed_software_fmt": {
        "zh-CN": "已在您的 <b>{model}</b> 上安装 <b>{software}</b>。",
        "en": "We've installed <b>{software}</b> on your <b>{model}</b>.",
        "fr": "Nous avons installé <b>{software}</b> sur votre <b>{model}</b>.",
        "es": "Hemos instalado <b>{software}</b> en tu <b>{model}</b>.",
    },
    "donate_installed_generic_fmt": {
        "zh-CN": "已在您的 <b>{model}</b> 上安装该软件。",
        "en": "We've installed the software on your <b>{model}</b>.",
        "fr": "Nous avons installé le logiciel sur votre <b>{model}</b>.",
        "es": "Hemos instalado el software en tu <b>{model}</b>.",
    },
    "sp_gui_prompt_steps_fmt": {
        "zh-CN": "• 如果设备尚未关机，请关闭您的 {label}。\n• 如果已连接 USB，请先拔出。\n• 在 SP Flash Tool 中，点击 Download（或根据需要选择 Format All + Download）。\n\n然后连接 USB 线缆开始刷机。",
        "en": "• If it isn't already off, power off your {label}.\n• If it is connected to USB, disconnect it first.\n• In SP Flash Tool, click Download (or Format All + Download as needed).\n\nThen connect the USB cable to begin flashing.",
        "fr": "• Si l'appareil n'est pas éteint, éteignez votre {label}.\n• S'il est connecté au port USB, débranchez-le d'abord.\n• Dans SP Flash Tool, cliquez sur Download (ou Format All + Download selon le besoin).\n\nConnectez ensuite le câble USB pour lancer le flashage.",
        "es": "• Si aún no está apagado, apaga tu {label}.\n• Si está conectado al USB, desconéctalo primero.\n• En SP Flash Tool, haz clic en Download (o Format All + Download según sea necesario).\n\nLuego conecta el cable USB para comenzar la instalación.",
    },
    "perm_setup_title": {
        "zh-CN": "权限设置",
        "en": "Permission Setup",
        "fr": "Configuration des permissions",
        "es": "Configuración de permisos",
    },
    "sel_type_a": {
        "zh-CN": "A",
        "en": "A",
        "fr": "A",
        "es": "A",
    },
    "sel_type_b": {
        "zh-CN": "B",
        "en": "B",
        "fr": "B",
        "es": "B",
    },
    "sel_type_help_btn": {
        "zh-CN": "类型？",
        "en": "Type?",
        "fr": "Type ?",
        "es": "¿Tipo?",
    },
    "sel_type_help_body": {
        "zh-CN": "请先尝试 A 型系统软件。\n\n如果安装后滚轮没有反应，请改为安装 B 型选项。\n\nB 型设备是指出厂时搭载 2.0 之前固件版本的设备。",
        "en": "Try Type A System Software first.\n\nIf your scroll wheel doesn't respond after installation, install one of the Type B options.\n\nType B devices are those that came with firmware versions earlier than 2.0.",
        "fr": "Essayez d'abord le logiciel système de Type A.\n\nSi la molette ne répond pas après l'installation, installez une option de Type B.\n\nLes appareils de Type B sont ceux livrés avec une version de firmware antérieure à 2.0.",
        "es": "Prueba primero el software de sistema de Tipo A.\n\nSi la rueda de desplazamiento no responde tras la instalación, instala una de las opciones de Tipo B.\n\nLos dispositivos Tipo B son los que se entregaron con versiones de firmware anteriores a 2.0.",
        "de": "Probieren Sie zuerst die Type-A-Systemsoftware.\n\nWenn das Scrollrad nach der Installation nicht reagiert, installieren Sie eine der Type-B-Optionen.\n\nType-B-Geräte sind Geräte, die mit einer Firmware-Version vor 2.0 ausgeliefert wurden.",
        "ja": "まず Type A のシステムソフトウェアをお試しください。\n\nインストール後にスクロールホイールが反応しない場合は、Type B のいずれかをインストールしてください。\n\nType B デバイスは、2.0 より前のファームウェアが入った状態で出荷されたデバイスです。",
    },
    "sel_type_help_title": {
        "zh-CN": "设备类型帮助",
        "en": "Device Type Help",
        "fr": "Aide sur le type d'appareil",
        "es": "Ayuda sobre el tipo de dispositivo",
    },
    "sel_install_tooltip_generic": {
        "zh-CN": "将所选系统软件安装或恢复到您的设备",
        "en": "Install or restore the selected system software to your device",
        "fr": "Installer ou restaurer le logiciel système sélectionné sur votre appareil",
        "es": "Instalar o restaurar el software de sistema seleccionado en tu dispositivo",
    },
    "sel_pre_install_title": {
        "zh-CN": "软件安装说明",
        "en": "Software Install Instructions",
        "fr": "Instructions d'installation du logiciel",
        "es": "Instrucciones de instalación del software",
    },
    "sel_n_releases_fmt": {
        "zh-CN": "{n} 个版本 — {action}",
        "en": "{n} release(s) — {action}",
        "fr": "{n} version(s) — {action}",
        "es": "{n} versión(es) — {action}",
    },
    "btn_power_lock": {
        "zh-CN": "电源/锁定键",
        "en": "power/lock button",
        "fr": "bouton marche/verrouillage",
        "es": "botón de encendido/bloqueo",
    },
    "dl_complete": {
        "zh-CN": "下载完成",
        "en": "Download complete",
        "fr": "Téléchargement terminé",
        "es": "Descarga completada",
    },
    "dl_starting": {
        "zh-CN": "正在开始下载…",
        "en": "Starting download…",
        "fr": "Démarrage du téléchargement…",
        "es": "Iniciando descarga…",
    },
    "dl_resuming_fmt": {
        "zh-CN": "从 {mb} MB 处继续下载…",
        "en": "Resuming download from {mb} MB...",
        "fr": "Reprise du téléchargement à {mb} Mo…",
        "es": "Reanudando la descarga desde {mb} MB…",
    },
    "dl_progress_fmt": {
        "zh-CN": "{dl} MB / {tot} MB（{pct}%）— {speed} MB/s",
        "en": "{dl} MB / {tot} MB ({pct}%) — {speed} MB/s",
        "fr": "{dl} Mo / {tot} Mo ({pct} %) — {speed} Mo/s",
        "es": "{dl} MB / {tot} MB ({pct} %) — {speed} MB/s",
    },
    "dl_progress_unknown_fmt": {
        "zh-CN": "{dl} MB — {speed} MB/s",
        "en": "{dl} MB — {speed} MB/s",
        "fr": "{dl} Mo — {speed} Mo/s",
        "es": "{dl} MB — {speed} MB/s",
    },
    "dl_retry_fmt": {
        "zh-CN": "连接中断，正在重试（{attempt}/{max}）…",
        "en": "Connection interrupted. Retrying ({attempt}/{max})...",
        "fr": "Connexion interrompue. Nouvelle tentative ({attempt}/{max})…",
        "es": "Conexión interrumpida. Reintentando ({attempt}/{max})…",
    },
    "linux_connecting_github": {
        "zh-CN": "正在连接 GitHub…",
        "en": "Connecting to GitHub...",
        "fr": "Connexion à GitHub…",
        "es": "Conectando con GitHub…",
    },
    "linux_engine_ready_msg": {
        "zh-CN": "刷机引擎已就绪。",
        "en": "Flashing engine ready.",
        "fr": "Moteur de flashage prêt.",
        "es": "Motor de instalación listo.",
    },
    "linux_setup_issue_fmt": {
        "zh-CN": "设置问题：{msg}",
        "en": "Setup issue: {msg}",
        "fr": "Problème de configuration : {msg}",
        "es": "Problema de configuración: {msg}",
    },
    "linux_unsupported_badge": {
        "zh-CN": "不支持",
        "en": "Unsupported",
        "fr": "Non pris en charge",
        "es": "No compatible",
    },
    "linux_engine_staged_desc": {
        "zh-CN": "MediaTek SP Flash Tool 已部署并兼容 libpng12。",
        "en": "MediaTek SP Flash Tool staged with libpng12 compatibility.",
        "fr": "MediaTek SP Flash Tool installé avec compatibilité libpng12.",
        "es": "MediaTek SP Flash Tool preparado con compatibilidad libpng12.",
    },
    "linux_engine_recheck_desc": {
        "zh-CN": "请点击“重新检查”或重新启动设置以下载组件。",
        "en": "Click Re-check or restart setup to download package.",
        "fr": "Cliquez sur Revérifier ou relancez la configuration pour télécharger le paquet.",
        "es": "Pulsa Volver a comprobar o reinicia la configuración para descargar el paquete.",
    },
    "linux_usb_rules_active_desc": {
        "zh-CN": "MediaTek 规则已生效（/etc/udev/rules.d/），已启用桌面访问。",
        "en": "MediaTek rules active (/etc/udev/rules.d/). Desktop access enabled.",
        "fr": "Règles MediaTek actives (/etc/udev/rules.d/). Accès bureau activé.",
        "es": "Reglas de MediaTek activas (/etc/udev/rules.d/). Acceso de escritorio habilitado.",
    },
    "linux_usb_grant_desc": {
        "zh-CN": "只需授权一次，应用即可与您的设备通信。",
        "en": "Grant permissions once so the app can communicate with your device.",
        "fr": "Accordez les permissions une seule fois pour que l'application puisse communiquer avec votre appareil.",
        "es": "Concede los permisos una vez para que la aplicación pueda comunicarse con tu dispositivo.",
    },
    "linux_perms_ok_title": {
        "zh-CN": "权限已配置",
        "en": "Permissions Configured",
        "fr": "Permissions configurées",
        "es": "Permisos configurados",
    },
    "linux_perms_ok_msg": {
        "zh-CN": "USB 权限和 udev 规则已成功安装，服务已配置。",
        "en": "USB permissions & udev rules installed successfully. Services configured.",
        "fr": "Permissions USB et règles udev installées avec succès. Services configurés.",
        "es": "Permisos USB y reglas udev instalados correctamente. Servicios configurados.",
    },
    "linux_copied_title": {
        "zh-CN": "已复制",
        "en": "Copied",
        "fr": "Copié",
        "es": "Copiado",
    },
    "linux_copied_msg_fmt": {
        "zh-CN": "设置命令已复制到剪贴板：\n\n{cmd}\n\n请在终端中运行以安装规则并重新加载 udev。",
        "en": "Setup command copied to clipboard:\n\n{cmd}\n\nRun this in a terminal to install rules and reload udev.",
        "fr": "Commande de configuration copiée dans le presse-papiers :\n\n{cmd}\n\nExécutez-la dans un terminal pour installer les règles et recharger udev.",
        "es": "Comando de configuración copiado al portapapeles:\n\n{cmd}\n\nEjecútalo en una terminal para instalar las reglas y recargar udev.",
    },
    "copy_btn": {
        "zh-CN": "复制",
        "en": "Copy",
        "fr": "Copier",
        "es": "Copiar",
    },
    "donate_rely_on_you": {
        "zh-CN": "我们依靠您的捐赠维持运行。立即捐赠",
        "en": "We rely on your donations to keep running. Donate now",
        "fr": "Nous comptons sur vos dons pour continuer. Faire un don",
        "es": "Dependemos de tus donaciones para seguir funcionando. Dona ahora",
    },
    "flash_sp_gui_tooltip": {
        "zh-CN": "打开 MediaTek SP Flash Tool 图形界面",
        "en": "Open MediaTek SP Flash Tool GUI",
        "fr": "Ouvrir l'interface MediaTek SP Flash Tool",
        "es": "Abrir la interfaz de MediaTek SP Flash Tool",
    },
    "btn_centre": {
        "zh-CN": "中心 (OK) 键",
        "en": "centre (OK) button",
        "en-US": "center (OK) button",
        "fr": "bouton central (OK)",
        "es": "botón central (OK)",
        "de": "Mittel-Taste (OK)",
        "ja": "中央 (OK) ボタン",
    },
    "btn_power": {
        "zh-CN": "电源键",
        "en": "power button",
        "fr": "bouton marche",
        "es": "botón de encendido",
        "de": "Ein-/Aus-Taste",
        "ja": "電源ボタン",
    },
    "install_power_on_steps_fmt": {
        "zh-CN": "请拔下您的 {label}，然后按住{button}直到开机。",
        "en": "Unplug your {label}, then hold the {button} until it turns on.",
        "fr": "Débranchez votre {label}, puis maintenez le {button} enfoncé jusqu'à ce qu'il s'allume.",
        "es": "Desconecta tu {label} y mantén pulsado el {button} hasta que se encienda.",
        "de": "Trennen Sie Ihr {label} und halten Sie die {button} gedrückt, bis es sich einschaltet.",
        "ja": "{label} を取り外し、電源が入るまで{button}を押し続けてください。",
    },
    "status_install_ok_fmt": {
        "zh-CN": "{software} 安装成功。{steps}",
        "en": "{software} installed successfully. {steps}",
        "fr": "{software} installé avec succès. {steps}",
        "es": "{software} instalado correctamente. {steps}",
        "de": "{software} wurde installiert. {steps}",
        "ja": "{software} のインストールが完了しました。{steps}",
    },
    "status_app_reset_defaults": {
        "zh-CN": "应用已完全恢复为默认设置，下载缓存已清除，电池提醒已恢复。",
        "en": "App completely reset to defaults, download cache cleared, and battery reminder restored.",
        "fr": "Application entièrement réinitialisée, cache de téléchargement vidé et rappel de batterie rétabli.",
        "es": "La aplicación se restableció por completo a los valores predeterminados, se borró la caché de descargas y se restauró el recordatorio de batería.",
        "de": "App vollständig auf die Standardeinstellungen zurückgesetzt, Download-Cache geleert und Akku-Erinnerung wiederhergestellt.",
        "ja": "アプリを初期設定に完全にリセットし、ダウンロードキャッシュを消去し、バッテリーのお知らせを復元しました。",
    },
    "log_app_reset_defaults": {
        "zh-CN": "应用配置已完全恢复为默认设置，下载缓存已清除，电池提醒已恢复（R）。",
        "en": "App configuration completely reset to defaults, download cache cleared, and battery reminder restored (R).",
        "fr": "Configuration de l'application entièrement réinitialisée, cache de téléchargement vidé et rappel de batterie rétabli (R).",
        "es": "La configuración de la aplicación se restableció por completo, se borró la caché de descargas y se restauró el recordatorio de batería (R).",
        "de": "App-Konfiguration vollständig zurückgesetzt, Download-Cache geleert und Akku-Erinnerung wiederhergestellt (R).",
        "ja": "アプリの設定を初期設定に完全にリセットし、ダウンロードキャッシュを消去し、バッテリーのお知らせを復元しました（R）。",
    },
    "flash_watch_waiting": {
        "zh-CN": "正在等待播放器重新启动……",
        "en": "Waiting for your player to restart…",
        "fr": "En attente du redémarrage de votre lecteur…",
        "es": "Esperando a que tu reproductor se reinicie…",
        "de": "Warte auf den Neustart deines Players…",
        "ja": "プレーヤーの再起動を待っています…",
    },
    "flash_watch_waiting_hint": {
        "zh-CN": "请保持连接——Rockbox 正在启动，主题将自动添加。",
        "en": "Keep it plugged in — Rockbox is starting up and its themes will be added automatically.",
        "fr": "Laissez-le branché — Rockbox démarre et ses thèmes seront ajoutés automatiquement.",
        "es": "Déjalo conectado: Rockbox se está iniciando y sus temas se añadirán automáticamente.",
        "de": "Lass es angesteckt — Rockbox startet und seine Themes werden automatisch hinzugefügt.",
        "ja": "接続したままにしてください。Rockbox が起動中で、テーマは自動的に追加されます。",
    },
    "flash_watch_found": {
        "zh-CN": "已检测到播放器——正在添加 Rockbox 主题……",
        "en": "Player detected — adding Rockbox themes…",
        "fr": "Lecteur détecté — ajout des thèmes Rockbox…",
        "es": "Reproductor detectado: añadiendo temas de Rockbox…",
        "de": "Player erkannt — Rockbox-Themes werden hinzugefügt…",
        "ja": "プレーヤーを検出しました。Rockbox テーマを追加しています…",
    },
    "flash_watch_timeout": {
        "zh-CN": "播放器尚未重新启动。",
        "en": "Your player has not come back yet.",
        "fr": "Votre lecteur n'est pas encore revenu.",
        "es": "Tu reproductor aún no ha vuelto.",
        "de": "Dein Player ist noch nicht zurück.",
        "ja": "プレーヤーがまだ戻ってきていません。",
    },
    "flash_watch_timeout_hint": {
        "zh-CN": "请检查是否已连接并开机。若仍然黑屏，请用针按住复位孔，并按住中心按钮（Y1）或电源键（其他机型）重新启动。若仍无法启动，说明固件未成功刷入，请安装其他固件。",
        "en": "Check that it is plugged in and switched on. If it stays dark, press a pin into the reset hole and hold the centre button (Y1) or the power button (other players) to restart it. If it still will not start, the firmware did not take — install another one.",
        "en-US": "Check that it is plugged in and turned on. If it stays dark, press a pin into the reset hole and hold the center button (Y1) or the power button (other players) to restart it. If it still will not start, the firmware did not take — install another one.",
        "fr": "Vérifiez qu'il est branché et allumé. S'il reste éteint, enfoncez une aiguille dans le trou de réinitialisation et maintenez le bouton central (Y1) ou le bouton d'alimentation (autres lecteurs) pour le redémarrer. S'il ne démarre toujours pas, le firmware n'a pas pris — installez-en un autre.",
        "es": "Comprueba que está conectado y encendido. Si sigue apagado, introduce un alfiler en el orificio de reinicio y mantén pulsado el botón central (Y1) o el botón de encendido (otros reproductores) para reiniciarlo. Si aún no arranca, el firmware no se aplicó: instala otro.",
        "de": "Prüfe, ob es angesteckt und eingeschaltet ist. Bleibt es dunkel, stecke eine Nadel in das Reset-Loch und halte die mittlere Taste (Y1) bzw. die Power-Taste (andere Player) gedrückt, um es neu zu starten. Startet es weiterhin nicht, hat die Firmware nicht gegriffen — installiere eine andere.",
        "ja": "接続と電源が入っているか確認してください。真っ暗なままなら、リセット穴にピンを差し、センターボタン（Y1）または電源ボタン（その他の機種）を押し続けて再起動します。それでも起動しない場合は、ファームウェアが入っていません。別のファームウェアをインストールしてください。",
    },
    "install_disconnect_guidance_fmt": {
        "zh-CN": "继续之前，请：\n\n1. 如果尚未关机，请关闭您的 {label}\n   （如有需要，可用针或回形针按下复位键）\n\n2. 如果已连接，请从 {label} 上拔下 USB 线\n\n然后点击“确定”并按照屏幕上的说明操作。",
        "en": "Before continuing, please:\n\n1. If it isn't already off, power off your {label}\n   (You can use a pin or paperclip to press the reset button if needed)\n\n2. If it is connected, disconnect the USB cable from your {label}\n\nThen click OK and follow the on-screen instructions.",
        "fr": "Avant de continuer :\n\n1. S'il n'est pas déjà éteint, éteignez votre {label}\n   (Vous pouvez utiliser une épingle ou un trombone pour appuyer sur le bouton de réinitialisation si nécessaire)\n\n2. S'il est connecté, débranchez le câble USB de votre {label}\n\nCliquez ensuite sur OK et suivez les instructions à l'écran.",
        "es": "Antes de continuar:\n\n1. Si aún no está apagado, apaga tu {label}\n   (Puedes usar un alfiler o un clip para pulsar el botón de reinicio si es necesario)\n\n2. Si está conectado, desconecta el cable USB de tu {label}\n\nLuego pulsa Aceptar y sigue las instrucciones en pantalla.",
    },

    # -----------------------------------------------------------------------
    # Flash progress / status actions. These are emitted by the flash
    # backends (FlashWorker.action_changed / _log) and shown verbatim in the
    # status panel and the Diagnostics log, so they must be translated at
    # emit time (same pattern as the dl_* keys used by downloads.py).
    # English values intentionally match the historical hardcoded strings.
    # -----------------------------------------------------------------------
    "action_searching": {
        "zh-CN": "正在搜索设备（请保持未连接）…",
        "en": "Searching for device (keep unplugged)...",
        "fr": "Recherche de l'appareil (laissez-le débranché)…",
        "es": "Buscando el dispositivo (mantenlo desconectado)...",
    },
    "action_brom_detected": {
        "zh-CN": "已检测到 BROM 模式设备…",
        "en": "Device detected in BROM mode...",
        "fr": "Appareil détecté en mode BROM…",
        "es": "Dispositivo detectado en modo BROM...",
    },
    "action_downloading_da": {
        "zh-CN": "正在向设备下载 DA…",
        "en": "Downloading DA to device...",
        "fr": "Téléchargement du DA vers l'appareil…",
        "es": "Descargando el DA al dispositivo...",
    },
    "action_downloading_bootloader": {
        "zh-CN": "正在下载 Bootloader…",
        "en": "Downloading Bootloader...",
        "fr": "Téléchargement du bootloader…",
        "es": "Descargando el bootloader...",
    },
    "action_format_succeeded": {
        "zh-CN": "擦除格式化成功…",
        "en": "Flash format succeeded...",
        "fr": "Le formatage a réussi…",
        "es": "El formateo se completó...",
    },
    "action_flash_complete": {
        "zh-CN": "刷机完成！请断开 USB 并重启设备。",
        "en": "Flash complete! Disconnect USB and reboot.",
        "fr": "Flash terminé ! Débranchez l'USB et redémarrez.",
        "es": "¡Flasheo completado! Desconecta el USB y reinicia.",
    },
    "action_writing_image_fmt": {
        "zh-CN": "正在写入固件镜像…（{pct}%）",
        "en": "Writing firmware image... ({pct}%)",
        "fr": "Écriture de l'image firmware… ({pct} %)",
        "es": "Escribiendo la imagen de firmware... ({pct} %)",
    },
    "action_writing_task_fmt": {
        "zh-CN": "{task}（{pct}%）",
        "en": "{task} ({pct}%)",
        "fr": "{task} ({pct} %)",
        "es": "{task} ({pct} %)",
    },
    "task_writing_system": {
        "zh-CN": "写入主要系统软件",
        "en": "Writing main system software",
        "fr": "Écriture du système principal",
        "es": "Escribiendo el sistema principal",
    },
    "task_writing_boot": {
        "zh-CN": "写入启动引导系统",
        "en": "Writing boot system",
        "fr": "Écriture du système de démarrage",
        "es": "Escribiendo el sistema de arranque",
    },
    "task_writing_recovery": {
        "zh-CN": "写入恢复系统软件",
        "en": "Writing recovery software",
        "fr": "Écriture du logiciel de récupération",
        "es": "Escribiendo el software de recuperación",
    },
    "task_writing_bootloader": {
        "zh-CN": "写入底层引导程序",
        "en": "Writing bootloader",
        "fr": "Écriture du bootloader",
        "es": "Escribiendo el gestor de arranque",
    },
    "task_writing_logo": {
        "zh-CN": "写入开机画面显示文件",
        "en": "Writing display logo",
        "fr": "Écriture du logo d'affichage",
        "es": "Escribiendo el logotipo de pantalla",
    },
    "task_preparing_storage": {
        "zh-CN": "准备用户存储空间",
        "en": "Preparing user storage",
        "fr": "Préparation de l'espace de stockage",
        "es": "Preparando el almacenamiento de usuario",
    },
    "task_writing_partition_table": {
        "zh-CN": "写入设备分区表",
        "en": "Writing device partition layout",
        "fr": "Écriture de la table des partitions",
        "es": "Escribiendo la tabla de particiones",
    },
    "task_writing_partition_generic": {
        "zh-CN": "写入设备固件组件",
        "en": "Writing device firmware component",
        "fr": "Écriture des composants du micrologiciel",
        "es": "Escribiendo componente de firmware",
    },
    "action_writing_part_fmt": {
        "zh-CN": "正在写入 {part}（{i}/{total}）：{file}",
        "en": "Writing {part} ({i}/{total}): {file}",
        "fr": "Écriture de {part} ({i}/{total}) : {file}",
        "es": "Escribiendo {part} ({i}/{total}): {file}",
    },
    "action_unpacking_fmt": {
        "zh-CN": "正在解包 {part}…",
        "en": "Unpacking {part}...",
        "fr": "Décompression de {part}…",
        "es": "Desempaquetando {part}...",
    },
    "action_recover_powercycle": {
        "zh-CN": "请将播放器断电重启（长按电源键约 10 秒），然后重新连接——正在重试…",
        "en": "Power-cycle the player (hold power ~10s), then reconnect it - retrying...",
        "fr": "Redémarrez le lecteur (maintenez le bouton marche ~10 s), puis reconnectez-le — nouvelle tentative…",
        "es": "Apaga y enciende el reproductor (mantén el botón de encendido ~10 s) y reconéctalo: reintentando...",
    },
    "action_init_mtkclient": {
        "zh-CN": "正在初始化 mtkclient…",
        "en": "Initializing mtkclient...",
        "fr": "Initialisation de mtkclient…",
        "es": "Inicializando mtkclient...",
    },
    "action_wait_mtk": {
        "zh-CN": "等待 MTK 设备…请先关闭设备电源，再连接 USB。",
        "en": "Waiting for MTK device... Power off device and connect USB.",
        "fr": "En attente de l'appareil MTK… Éteignez l'appareil puis connectez l'USB.",
        "es": "Esperando el dispositivo MTK... Apaga el dispositivo y conecta el USB.",
    },
    "action_da_stall": {
        "zh-CN": "配置下载代理时设备停止响应。请将设备完全关机，重新连接后重试。",
        "en": "Device stopped responding while configuring the download agent. Power it off completely, reconnect, and retry.",
        "fr": "L'appareil a cessé de répondre pendant la configuration de l'agent de téléchargement. Éteignez-le complètement, reconnectez-le et réessayez.",
        "es": "El dispositivo dejó de responder mientras se configuraba el agente de descarga. Apágalo por completo, reconéctalo y reintenta.",
    },
    "action_detected_brom": {
        "zh-CN": "已检测到设备（BROM 模式）——正在配置下载代理…",
        "en": "Device detected (BROM mode) - configuring download agent...",
        "fr": "Appareil détecté (mode BROM) — configuration de l'agent de téléchargement…",
        "es": "Dispositivo detectado (modo BROM): configurando el agente de descarga...",
    },
    "action_detected_preloader": {
        "zh-CN": "已检测到设备（preloader 模式）——正在配置下载代理…",
        "en": "Device detected (preloader mode) - configuring download agent...",
        "fr": "Appareil détecté (mode preloader) — configuration de l'agent de téléchargement…",
        "es": "Dispositivo detectado (modo preloader): configurando el agente de descarga...",
    },
    "action_da_wait_back": {
        "zh-CN": "启动下载代理时播放器停止响应。正在等待其恢复…",
        "en": "The player stopped responding while starting the download agent. Waiting for it to come back...",
        "fr": "Le lecteur a cessé de répondre au démarrage de l'agent de téléchargement. En attente de son retour…",
        "es": "El reproductor dejó de responder al iniciar el agente de descarga. Esperando a que vuelva...",
    },
    "action_device_connected": {
        "zh-CN": "设备已连接——正在准备写入命令…",
        "en": "Device connected - preparing write commands...",
        "fr": "Appareil connecté — préparation des commandes d'écriture…",
        "es": "Dispositivo conectado: preparando los comandos de escritura...",
    },
    "action_install_in_progress": {
        "zh-CN": "正在安装",
        "en": "Install in Progress",
        "fr": "Installation en cours",
        "es": "Instalación en curso",
    },
    "action_formatting": {
        "zh-CN": "正在格式化 userdata 和 cache 分区…",
        "en": "Formatting userdata and cache...",
        "fr": "Formatage des partitions userdata et cache…",
        "es": "Formateando userdata y cache...",
    },
    "action_restart_brom": {
        "zh-CN": "正在将播放器重启进入 BROM 模式…",
        "en": "Restarting the player into BROM mode...",
        "fr": "Redémarrage du lecteur en mode BROM…",
        "es": "Reiniciando el reproductor en modo BROM...",
    },
    "log_install_writing": {
        "zh-CN": "正在安装——正在写入固件镜像…",
        "en": "Install in Progress - writing firmware images...",
        "fr": "Installation en cours — écriture des images firmware…",
        "es": "Instalación en curso: escribiendo las imágenes de firmware...",
    },
    "log_sparse_fmt": {
        "zh-CN": "检测到 {part} 为 Android sparse 镜像，正在进程内转换…",
        "en": "Detected Android sparse image for {part}, converting in-process...",
        "fr": "Image Android sparse détectée pour {part}, conversion en cours…",
        "es": "Imagen sparse de Android detectada para {part}, convirtiendo en el proceso...",
    },

    # Error-page codes (FlashWorker.finished error_code values shown in the
    # Error Code row; the raw code stays visible in parentheses for support).
    "err_code_user_cancelled": {
        "zh-CN": "用户已取消",
        "en": "Cancelled by user",
        "fr": "Annulé par l'utilisateur",
        "es": "Cancelado por el usuario",
    },
    "err_code_connection_failed": {
        "zh-CN": "无法连接设备",
        "en": "Could not connect to the device",
        "fr": "Impossible de se connecter à l'appareil",
        "es": "No se pudo conectar al dispositivo",
    },
    "err_code_mtk_init_failed": {
        "zh-CN": "MTKClient 启动失败",
        "en": "MTKClient failed to start",
        "fr": "Échec du démarrage de MTKClient",
        "es": "MTKClient no pudo iniciarse",
    },
    "err_code_mtk_import_failed": {
        "zh-CN": "无法加载 MTKClient",
        "en": "MTKClient could not be loaded",
        "fr": "Impossible de charger MTKClient",
        "es": "No se pudo cargar MTKClient",
    },
    "err_code_sp_not_found": {
        "zh-CN": "未找到 SP Flash Tool",
        "en": "SP Flash Tool not found",
        "fr": "SP Flash Tool introuvable",
        "es": "No se encontró SP Flash Tool",
    },
    "err_code_no_scatter": {
        "zh-CN": "刷机包中缺少 scatter 文件",
        "en": "No scatter file in the package",
        "fr": "Aucun fichier scatter dans le paquet",
        "es": "El paquete no contiene archivo scatter",
    },
    "err_code_missing_images": {
        "zh-CN": "刷机包中缺少固件镜像",
        "en": "Firmware images missing from the package",
        "fr": "Images firmware manquantes dans le paquet",
        "es": "Faltan imágenes de firmware en el paquete",
    },
    "err_code_internal": {
        "zh-CN": "内部错误",
        "en": "Internal error",
        "fr": "Erreur interne",
        "es": "Error interno",
    },
    # Backend failures explained in the user's terms. The raw tool name/code
    # stays in the Error Code row above them.
    "err_sp_wrong_model_title": {
        "zh-CN": "连接了错误的型号",
        "en": "Wrong Model Connected",
        "fr": "Mauvais modèle connecté",
        "es": "Modelo incorrecto conectado",
    },
    "err_sp_wrong_model": {
        "zh-CN": "连接的播放器型号与本刷机包的目标型号不同。请断开该设备，然后为此播放器安装正确的固件。",
        "en": "The connected player is a different model than this package targets. Disconnect it, then install the firmware built for this player.",
        "fr": "Le lecteur connecté n'est pas le modèle visé par ce paquet. Débranchez-le, puis installez le firmware prévu pour ce lecteur.",
        "es": "El reproductor conectado no es el modelo al que apunta este paquete. Desconéctelo e instale el firmware de este reproductor.",
    },
    "err_sp_timeout": {
        "zh-CN": "工具没有检测到播放器。请让播放器保持关机并断开，然后在提示连接时再插入 USB。",
        "en": "The tool never saw the player. Keep it powered off and unplugged, then connect the USB cable when prompted.",
        "fr": "L'outil n'a pas vu le lecteur. Laissez-le éteint et débranché, puis branchez le câble USB à l'invite.",
        "es": "La herramienta no detectó el reproductor. Déjelo apagado y desconectado y conecte el USB cuando se le indique.",
    },
    "err_sp_brom_startcmd": {
        "zh-CN": "播放器启动过早。请完全关机后重新连接。",
        "en": "The player powered up too early. Power it off completely, then connect it again.",
        "fr": "Le lecteur s'est allumé trop tôt. Éteignez-le complètement, puis rebranchez-le.",
        "es": "El reproductor se encendió demasiado pronto. Apáguelo por completo y vuelva a conectarlo.",
    },
    "err_sp_download_fail": {
        "zh-CN": "写入被中断。请检查 USB 线与端口以及电池电量，然后重试。",
        "en": "Writing was interrupted. Check the USB cable and port and the battery charge, then try again.",
        "fr": "L'écriture a été interrompue. Vérifiez le câble et le port USB ainsi que la batterie, puis réessayez.",
        "es": "La escritura se interrumpió. Compruebe el cable y el puerto USB y la batería, y vuelva a intentarlo.",
    },
    "err_sp_da_init_sync": {
        "zh-CN": "下载代理（DA）无法启动。请尝试另一种刷机方式。",
        "en": "The download agent could not start. Try the other flash method.",
        "fr": "L'agent de téléchargement n'a pas pu démarrer. Essayez l'autre méthode de flashage.",
        "es": "El agente de descarga no pudo iniciarse. Pruebe el otro método de flasheo.",
    },
    "err_sp_brom_da_fail": {
        "zh-CN": "无法向播放器发送下载代理。请将播放器关机后重新连接。",
        "en": "The download agent could not be sent to the player. Power it off and reconnect.",
        "fr": "L'agent de téléchargement n'a pas pu être envoyé. Éteignez le lecteur et rebranchez-le.",
        "es": "No se pudo enviar el agente de descarga. Apague el reproductor y vuelva a conectarlo.",
    },
    "err_sp_com_port": {
        "zh-CN": "工具无法打开串口。请关闭其他刷机工具后重新连接。",
        "en": "The tool could not open its serial port. Close other flashing tools and reconnect.",
        "fr": "L'outil n'a pas pu ouvrir son port série. Fermez les autres outils de flashage et rebranchez.",
        "es": "La herramienta no pudo abrir su puerto serie. Cierre otras herramientas de flasheo y vuelva a conectar.",
    },
    "err_com_port_reboot_suggestion": {
        "zh-CN": "USB 串口无法打开（错误 1013）。重启电脑将释放被锁定的端口。请重启电脑后再试。",
        "en": "The USB COM port could not be opened (error 1013). Restarting your computer will release the locked port. Please restart your computer and try again.",
        "fr": "Impossible d'ouvrir le port COM USB (erreur 1013). Le redémarrage de votre ordinateur libérera le port verrouillé. Veuillez redémarrer votre ordinateur et réessayer.",
        "es": "No se pudo abrir el puerto COM USB (error 1013). Reiniciar tu equipo liberará el puerto bloqueado. Reinicia tu equipo e inténtalo de nuevo.",
        "de": "Der USB-COM-Port konnte nicht geöffnet werden (Fehler 1013). Ein Neustart Ihres Computers gibt den gesperrten Anschluss frei. Bitte starten Sie Ihren Computer neu und versuchen Sie es erneut.",
        "ja": "USB COMポートを開くことができませんでした（エラー 1013）。コンピューターを再起動するとロックされたポートが解放されます。コンピューターを再起動して再試行してください。",
    },
    "err_sp_enable_dram": {
        "zh-CN": "工具无法初始化播放器的内存。请使用专为此型号构建的刷机包。",
        "en": "The tool could not start the player's memory. Use the package built for this exact model.",
        "fr": "L'outil n'a pas pu initialiser la mémoire du lecteur. Utilisez le paquet prévu pour ce modèle.",
        "es": "La herramienta no pudo iniciar la memoria del reproductor. Use el paquete de este modelo exacto.",
    },
    "err_sp_storage_read": {
        "zh-CN": "工具无法读取播放器的存储。请检查连接；设备可能存在硬件故障。",
        "en": "The tool could not read the player's storage. Check the connection; the device may have a hardware fault.",
        "fr": "L'outil n'a pas pu lire le stockage du lecteur. Vérifiez la connexion ; l'appareil a peut-être un défaut matériel.",
        "es": "La herramienta no pudo leer el almacenamiento del reproductor. Compruebe la conexión; puede haber un fallo de hardware.",
    },
    "err_attempt_timeout_title": {
        "zh-CN": "设备无响应",
        "en": "The device did not respond",
        "fr": "L'appareil n'a pas répondu",
        "es": "El dispositivo no respondió",
    },
    "err_attempt_timeout": {
        "zh-CN": "刷机工具在 {seconds} 秒内没有检测到进度，已停止本次尝试。",
        "en": "The flashing tool showed no progress within {seconds} seconds, so this attempt was stopped.",
        "fr": "L'outil de flashage n'a montré aucun progrès en {seconds} secondes ; cette tentative a été arrêtée.",
        "es": "La herramienta de flasheo no mostró progreso en {seconds} segundos; se detuvo este intento.",
    },
    "err_connection_failed_title": {
        "zh-CN": "连接失败",
        "en": "Connection Failed",
        "fr": "Échec de connexion",
        "es": "Error de conexión",
        "de": "Verbindung fehlgeschlagen",
        "ja": "接続に失敗しました",
    },
    "err_connection_failed_desc": {
        "zh-CN": "无法与播放器建立稳定连接。请确保设备已完全关机（必要时用卡针按复位孔），拔下 USB，在提示时重新连接。",
        "en": "Could not establish a stable connection with the player. Ensure the device is powered off (use a pin to press the reset button if needed), unplug USB, and reconnect when prompted.",
        "fr": "Impossible d'établir une connexion stable avec le lecteur. Assurez-vous que l'appareil est éteint (utilisez un trombone pour le bouton de réinitialisation si nécessaire), débranchez l'USB et rebranchez à l'invite.",
        "es": "No se pudo establecer una conexión estable con el reproductor. Asegúrese de que el dispositivo esté apagado (use un clip para presionar el botón de reinicio si es necesario), desconecte el USB y vuelva a conectarlo cuando se le indique.",
        "de": "Es konnte keine stabile Verbindung zum Player hergestellt werden. Stellen Sie sicher, dass das Gerät ausgeschaltet ist (ggf. Büroklammer für Reset-Taste verwenden), USB trennen und bei Aufforderung wieder anschließen.",
        "ja": "プレイヤーとの安定した接続を確立できませんでした。電源が切れていることを確認し（必要に応じてピンでリセットボタンを押す）、USBを取り外してプロンプトが表示されたら再接続してください。",
    },
    "err_flash_write_failed_title": {
        "zh-CN": "写入中断",
        "en": "Write Interrupted",
        "fr": "Écriture interrompue",
        "es": "Escritura interrumpida",
        "de": "Schreibvorgang unterbrochen",
        "ja": "書き込みが中断されました",
    },
    "err_flash_write_failed_desc": {
        "zh-CN": "刷机被中断或写入失败。请检查 USB 数据线是否插紧且电池电量充足，然后重试。",
        "en": "Flashing was interrupted or the write operation failed. Check that the USB cable is firmly plugged in and the battery has sufficient charge, then try again.",
        "fr": "Le flashage a été interrompu ou l'opération d'écriture a échoué. Vérifiez que le câble USB est bien branché et que la batterie est chargée, puis réessayez.",
        "es": "El flasheo se interrumpió o falló la operación de escritura. Compruebe que el cable USB esté bien conectado y la batería cargada, luego inténtelo de nuevo.",
        "de": "Der Flashvorgang wurde unterbrochen oder das Schreiben ist fehlgeschlagen. Prüfen Sie das USB-Kabel und den Akkustand, und versuchen Sie es erneut.",
        "ja": "フラッシュが中断されたか、書き込み操作に失敗しました。USBケーブルがしっかり接続され、バッテリー残量が十分であることを確認して、再試行してください。",
    },
    "err_mtk_output_stall_title": {
        "zh-CN": "刷机卡住了",
        "en": "The flash appears to be stuck",
        "fr": "Le flash semble bloqué",
        "es": "El flasheo parece atascado",
        "de": "Das Flashen scheint hängen geblieben",
        "ja": "フラッシュが停滞しているようです",
    },
    "err_mtk_output_stall_desc": {
        "zh-CN": "刷机工具在最近的 {seconds} 秒内仍在显示相同内容，好像没有继续写入固件。刷机可能失败了。如果这是最后一次尝试（包括所有安装方式），或此设备只能使用 MTKClient，请重启电脑后再试。",
        "en": "The flashing tool has shown the same line for the last {seconds} seconds and does not appear to be continuing to write the firmware. The flash may have failed. If this was the last of two attempts with each method on a system that can fall back between them, or if only MTKClient is available on this machine, restart the computer and try again.",
        "fr": "L'outil de flashage affiche la même ligne depuis les dernières {seconds} secondes et ne semble pas continuer à écrire le firmware. Le flash a peut-être échoué. Si c'était le dernier de deux essais avec chaque méthode sur un système qui peut basculer entre elles, ou si seul MTKClient est disponible sur cette machine, redémarrez l'ordinateur et réessayez.",
        "es": "La herramienta de flasheo ha mostrado la misma línea durante los últimos {seconds} segundos y no parece seguir escribiendo el firmware. El flasheo puede haber fallado. Si fue el último de dos intentos con cada método en un sistema que puede cambiar entre ellos, o si solo está disponible MTKClient en esta máquina, reinicia el equipo e inténtalo de nuevo.",
        "de": "Das Flashtool hat in den letzten {seconds} Sekunden dieselbe Zeile angezeigt und scheint nicht weiter das Firmware zu schreiben. Ein Flashen ist möglicherweise fehlgeschlagen. Wenn dies der letzte von zwei Versuchen mit jeder Methode auf einem System war, das zwischen ihnen ausweichen kann, oder wenn nur MTKClient auf dieser Maschine verfügbar ist, starte den Computer neu und versuche es erneut.",
        "ja": "フラッシュツールは直近の {seconds} 秒間同じ行を表示しており、ファームウェアの書き込みを継続していないようです。フラッシュに失敗した可能性があります。これは、バックエンド間で切り替えできるシステムで各バックエンドの2回の試行の最後の1回だった場合、またはこのマシンでMTKClientしか利用できない場合は、コンピューターを再起動して再試行してください。",
    },
    "err_only_mtkclient_title": {
        "zh-CN": "只有 MTKClient 可用",
        "en": "Only MTKClient is available",
        "fr": "Seul MTKClient est disponible",
        "es": "Solo MTKClient está disponible",
        "de": "Nur MTKClient verfügbar",
        "ja": "MTKClientのみ利用できます",
    },
    "err_only_mtkclient_desc": {
        "zh-CN": "此设备只能使用 MTKClient 安装。刷机可能失败了。请重启电脑后再试。",
        "en": "Only MTKClient is available for this install. The flash may have failed. Please restart the computer and try again.",
        "fr": "Seul MTKClient est disponible pour cette installation. Le flash a peut-être échoué. Veuillez redémarrer l'ordinateur et réessayer.",
        "es": "Solo MTKClient está disponible para esta instalación. El flasheo puede haber fallado. Reinicia el equipo e inténtalo de nuevo.",
        "de": "Für diese Installation ist nur MTKClient verfügbar. Ein Flashen ist möglicherweise fehlgeschlagen. Bitte den Computer neu starten und erneut versuchen.",
        "ja": "このインストールにはMTKClientしか利用できません。フラッシュに失敗した可能性があります。コンピューターを再起動して再試行してください。",
    },
    "err_mtk_output_stall_fallback_available": {
        "zh-CN": "此电脑也可以使用 SP Flash Tool 安装。如果可以，请切换到 SP Flash Tool 重试。",
        "en": "This computer can also install using SP Flash Tool. If you can, switch to SP Flash Tool and try again.",
        "fr": "Cet ordinateur peut aussi installer avec SP Flash Tool. Si vous le pouvez, basculez sur SP Flash Tool et réessayez.",
        "es": "Esta computadora también puede instalar con SP Flash Tool. Si puedes, cambia a SP Flash Tool e inténtalo de nuevo.",
        "de": "Dieser Computer kann auch mit SP Flash Tool installieren. Wenn möglich, wechsle zu SP Flash Tool und versuche es erneut.",
        "ja": "このコンピューターはSP Flash Toolでもインストールできます。可能であればSP Flash Toolに切り替えて再試行してください。",
    },
    "prompt_restart_and_quit_title": {
        "zh-CN": "请重启电脑",
        "en": "Restart your computer",
        "fr": "Redémarrez votre ordinateur",
        "es": "Reinicia tu equipo",
        "de": "Starte den Computer neu",
        "ja": "コンピューターを再起動してください",
    },
    "prompt_restart_and_quit_desc": {
        "zh-CN": "刷机可能失败了。请阅读上述信息，重启电脑后再试。如果你点击确定，应用将退出以便你重启。",
        "en": "The flash may have failed. Please read the message above and restart the computer before trying again. If you click OK, the app will quit so you can restart.",
        "fr": "Le flash a peut-être échoué. Lisez le message ci-dessus et redémarrez l'ordinateur avant de réessayer. Si vous cliquez sur OK, l'application se fermera pour vous permettre de redémarrer.",
        "es": "El flasheo puede haber fallado. Lee el mensaje de arriba y reinicia el equipo antes de intentarlo de nuevo. Si haces clic en Aceptar, la aplicación se cerrará para que puedas reiniciar.",
        "de": "Das Flashen ist möglicherweise fehlgeschlagen. Lies die obige Meldung und starte den Computer neu, bevor du es erneut versuchst. Wenn du auf OK klickst, wird die Anwendung beendet, damit du neu starten kannst.",
        "ja": "フラッシュに失敗した可能性があります。上記のメッセージを読んでから再試行する前にコンピューターを再起動してください。[OK] をクリックすると、アプリケーションが終了して再起動できるようになります。",
    },
    "prompt_restart_and_quit_ok": {
        "zh-CN": "确定",
        "en": "OK",
        "fr": "OK",
        "es": "Aceptar",
        "de": "OK",
        "ja": "OK",
    },
    "err_missing_package_files_title": {
        "zh-CN": "固件包无效或不完整",
        "en": "Invalid Firmware Package",
        "fr": "Paquet de firmware invalide",
        "es": "Paquete de firmware no válido",
        "de": "Ungültiges Firmware-Paket",
        "ja": "無効なファームウェアパッケージ",
    },
    "err_missing_package_files_desc": {
        "zh-CN": "刷机包缺少 scatter 文件或必要的镜像文件。请重新下载固件包或选择完整的固件文件。",
        "en": "The firmware package is missing the scatter file or required image files. Please re-download the package or select a complete firmware file.",
        "fr": "Le paquet de firmware ne contient pas le fichier scatter ou des images requises. Veuillez retélécharger le paquet ou choisir un fichier complet.",
        "es": "Al paquete de firmware le falta el archivo scatter o imágenes necesarias. Vuelva a descargar el paquete o elija un archivo de firmware completo.",
        "de": "Dem Firmware-Paket fehlt die Scatter-Datei oder erforderliche Image-Dateien. Bitte laden Sie das Paket erneut herunter oder wählen Sie eine vollständige Datei.",
        "ja": "ファームウェアパッケージにscatterファイルまたは必要なイメージファイルが含まれていません。パッケージを再ダウンロードするか完全なファームウェアファイルを選択してください。",
    },
    "err_tool_unavailable_title": {
        "zh-CN": "刷机工具不可用",
        "en": "Flashing Tool Unavailable",
        "fr": "Outil de flashage indisponible",
        "es": "Herramienta de flasheo no disponible",
        "de": "Flash-Tool nicht verfügbar",
        "ja": "フラッシュツールを利用できません",
    },
    "err_tool_unavailable_desc": {
        "zh-CN": "刷机工具或所需的系统驱动无法初始化。请检查系统权限、驱动设置或重新安装应用程序。",
        "en": "The flashing tool or required system drivers could not be initialized. Please check system permissions, driver setup, or reinstall the application.",
        "fr": "L'outil de flashage ou les pilotes nécessaires n'ont pas pu être initialisés. Vérifiez les autorisations système ou réinstallez l'application.",
        "es": "No se pudo iniciar la herramienta de flasheo o los controladores requeridos. Verifique los permisos del sistema o reinstale la aplicación.",
        "de": "Das Flash-Tool oder erforderliche Treiber konnten nicht initialisiert werden. Bitte Berechtigungen prüfen oder Anwendung neu installieren.",
        "ja": "フラッシュツールまたは必要なドライバを初期化できませんでした。システムの権限を確認するかアプリケーションを再インストールしてください。",
    },
    "err_driver_missing_title": {
        "zh-CN": "需要安装联发科 USB 驱动",
        "en": "MediaTek USB Driver Required",
        "fr": "Pilote USB MediaTek requis",
        "es": "Se requiere el controlador USB MediaTek",
        "de": "MediaTek-USB-Treiber erforderlich",
        "ja": "MediaTek USBドライバーが必要です",
    },
    "err_driver_missing_desc": {
        "zh-CN": "未安装联发科 USB 驱动时可能会出现此错误。请点击“下载驱动”安装联发科 USB 驱动，重启电脑后再试。",
        "en": "This error can occur when the MediaTek USB driver is not installed. Click Download Drivers to install the MediaTek USB driver, restart your computer, and try again.",
        "fr": "Cette erreur peut se produire lorsque le pilote USB MediaTek n'est pas installé. Cliquez sur Télécharger les pilotes pour installer le pilote USB MediaTek, redémarrez votre ordinateur, puis réessayez.",
        "es": "Este error puede ocurrir cuando el controlador USB de MediaTek no está instalado. Haga clic en Descargar controladores para instalar el controlador USB de MediaTek, reinicie el ordenador e inténtelo de nuevo.",
        "de": "Dieser Fehler kann auftreten, wenn der MediaTek-USB-Treiber nicht installiert ist. Klicken Sie auf Treiber herunterladen, installieren Sie den MediaTek-USB-Treiber, starten Sie den Computer neu und versuchen Sie es erneut.",
        "ja": "このエラーは、MediaTek USBドライバーがインストールされていない場合に発生することがあります。「ドライバーをダウンロード」をクリックしてMediaTek USBドライバーをインストールし、コンピューターを再起動してから再試行してください。",
    },
    "err_cache_file_missing_desc": {
        "en": "A cached firmware file is missing. Choose Select Software to rebuild the extracted files or download the firmware archive again. This is a local file problem, not a USB connection issue.",
        "zh-CN": "缓存固件文件缺失。请在“选择软件”中重新提取固件文件或下载固件压缩包。这是本地文件问题，与 USB 连接无关。",
        "fr": "Un fichier du firmware en cache est manquant. Dans Sélectionner un logiciel, reconstituez les fichiers extraits ou téléchargez à nouveau l'archive. Il s'agit d'un problème de fichier local, pas de connexion USB.",
        "es": "Falta un archivo de firmware en caché. En Seleccionar software, vuelve a extraer los archivos o descarga el archivo de firmware de nuevo. Es un problema de archivo local, no de conexión USB.",
        "de": "Eine zwischengespeicherte Firmware-Datei fehlt. Wählen Sie unter Software auswählen die Firmware erneut aus, um die Dateien zu entpacken oder das Archiv erneut herunterzuladen. Dies ist ein lokales Dateiproblem, kein USB-Verbindungsproblem.",
        "ja": "キャッシュされたファームウェアファイルがありません。「ソフトウェアを選択」からファイルを再展開するか、アーカイブを再ダウンロードしてください。USB接続の問題ではありません。",
    },
    "sel_cache_retry_button": {
        "en": "Select Software",
        "zh-CN": "选择软件",
        "fr": "Sélectionner un logiciel",
        "es": "Seleccionar software",
        "de": "Software auswählen",
        "ja": "ソフトウェアを選択",
    },
    "sel_cache_retry_tooltip": {
        "en": "Return to Select Software to rebuild or download the firmware package.",
        "zh-CN": "返回“选择软件”以重新提取或下载固件。",
        "fr": "Retournez à Sélectionner un logiciel pour reconstituer ou télécharger le firmware.",
        "es": "Vuelve a Seleccionar software para reconstruir o descargar el firmware.",
        "de": "Zurück zu Software auswählen, um die Firmware neu zu entpacken oder herunterzuladen.",
        "ja": "ソフトウェア選択に戻り、ファームウェアを再展開またはダウンロードします。",
    },
    "sel_cache_retry_select_release": {
        "en": "Select a firmware release to download again.",
        "zh-CN": "请选择要重新下载的固件版本。",
        "fr": "Sélectionnez une version du firmware à télécharger à nouveau.",
        "es": "Selecciona una versión de firmware para volver a descargarla.",
        "de": "Wählen Sie eine Firmware-Version zum erneuten Herunterladen aus.",
        "ja": "再ダウンロードするファームウェアのリリースを選択してください。",
    },
    "err_generic_flash_failed_desc": {
        "zh-CN": "安装过程中发生异常错误（{detail}）。请将播放器完全关机，在提示时重新连接并重试。",
        "en": "An unexpected error occurred during installation ({detail}). Power off the player completely, reconnect when prompted, and try again.",
        "fr": "Une erreur inattendue est survenue pendant l'installation ({detail}). Éteignez complètement le lecteur, reconnectez à l'invite et réessayez.",
        "es": "Ocurrió un error inesperado durante la instalación ({detail}). Apague el reproductor por completo, vuelva a conectarlo cuando se le indique e inténtelo de nuevo.",
        "de": "Ein unerwarteter Fehler ist während der Installation aufgetreten ({detail}). Schalten Sie das Gerät komplett aus, verbinden Sie es bei Aufforderung erneut und versuchen Sie es noch einmal.",
        "ja": "インストール中に予期しないエラーが発生しました（{detail}）。プレイヤーの電源を完全に切り、プロンプトが表示されたら再接続して再試行してください。",
    },
    "flash_attempt_timeout": {
        "zh-CN": "正在等待设备… 剩余 {seconds} 秒",
        "en": "Waiting for the player… {seconds}s left",
        "fr": "En attente du lecteur… {seconds} s restantes",
        "es": "Esperando al reproductor… quedan {seconds} s",
    },
    "flash_method_confirm": {
        "zh-CN": "刷机方式已改为 {method}。请确认设备已断开并关机，然后点击“下一步”。",
        "en": "Install method changed to {method}. Confirm the player is unplugged and powered off, then click Next.",
        "fr": "Méthode changée pour {method}. Confirmez que le lecteur est débranché et éteint, puis cliquez sur Suivant.",
        "es": "Método cambiado a {method}. Confirme que el reproductor está desconectado y apagado y pulse Siguiente.",
    },
    "flash_failover_note": {
        "zh-CN": "{method} 未能开始刷机。在设备关机并断开后，将改用 {other}。",
        "en": "{method} did not get the install started. After the player is powered off and unplugged, {other} will be used instead.",
        "fr": "{method} n'a pas lancé l'installation. Une fois le lecteur éteint et débranché, {other} sera utilisé.",
        "es": "{method} no inició la instalación. Con el reproductor apagado y desconectado se usará {other}.",
    },
    "themepack_card_title": {
        "zh-CN": "安装 360p Rockbox 主题包",
        "en": "Install 360p Rockbox Theme Pack",
        "fr": "Installer le pack de thèmes Rockbox 360p",
        "es": "Instalar paquete de temas Rockbox 360p",
    },
    "themepack_card_desc": {
        "zh-CN": "包含专为 360p 屏幕打造的精选社区主题、高清背景和字体。",
        "en": "Includes beautiful community themes, high-resolution backdrops, and fonts crafted specifically for 360p displays.",
        "fr": "Comprend de superbes thèmes communautaires, des arrière-plans haute résolution et des polices conçues pour les écrans 360p.",
        "es": "Incluye magníficos temas de la comunidad, fondos de pantalla de alta resolución y fuentes diseñadas para pantallas de 360p.",
    },
    "themepack_install_btn": {
        "zh-CN": "获取 360p 主题包",
        "en": "Get 360p Theme Pack",
        "fr": "Obtenir le pack de thèmes 360p",
        "es": "Obtener paquete de temas 360p",
    },
    "themepack_installed_success": {
        "zh-CN": "✓ 360p 主题包已成功安装！",
        "en": "✓ 360p Theme Pack installed successfully!",
        "fr": "✓ Pack de thèmes 360p installé avec succès !",
        "es": "✓ ¡Paquete de temas 360p instalado con éxito!",
    },
    "themepack_dialog_title": {
        "zh-CN": "Rockbox 360p 主题包安装",
        "en": "Rockbox 360p Theme Pack Setup",
        "fr": "Installation du pack de thèmes Rockbox 360p",
        "es": "Instalación del paquete de temas Rockbox 360p",
    },
    "themepack_step1_title": {
        "zh-CN": "第 1 步：以 USB 存储模式连接",
        "en": "Step 1: Connect in USB Storage Mode",
        "fr": "Étape 1 : Connexion en mode stockage USB",
        "es": "Paso 1: Conectar en modo de almacenamiento USB",
    },
    "themepack_step1_desc": {
        "zh-CN": "1. 长按电源键开启播放器。\n2. 进入 Rockbox 系统后，用 USB 数据线连接电脑。\n3. 如播放器屏幕弹出提示，确认进入 USB 存储模式，使设备作为驱动器显示在电脑上。",
        "en": "1. Turn on your player by holding the Power button.\n2. When Rockbox boots, plug the USB cable into your computer.\n3. If prompted on the player screen, confirm USB Storage mode so the device appears as a drive.",
        "fr": "1. Allumez votre lecteur en maintenant le bouton Marche/Arrêt.\n2. Lorsque Rockbox démarre, branchez le câble USB à votre ordinateur.\n3. Si vous y êtes invité, confirmez le mode stockage USB afin que l'appareil apparaisse comme un lecteur.",
        "es": "1. Encienda su reproductor manteniendo pulsado el botón de encendido.\n2. Cuando se inicie Rockbox, conecte el cable USB a su ordenador.\n3. Si se le solicita en la pantalla, confirme el modo de almacenamiento USB para que el dispositivo aparezca como una unidad.",
    },
    "themepack_step2_title": {
        "zh-CN": "第 2 步：选择播放器上的任意文件夹",
        "en": "Step 2: Select Any Folder on the Player",
        "fr": "Étape 2 : Sélectionnez un dossier sur le lecteur",
        "es": "Paso 2: Seleccione cualquier carpeta del reproductor",
    },
    "themepack_step2_desc": {
        "zh-CN": "由于操作系统（如 macOS）默认隐藏点文件（如 .rockbox），您可以选择播放器驱动器上的任意文件夹或磁盘根目录。我们将自动为您定位或创建 .rockbox 目录。",
        "en": "Because operating systems (such as macOS) hide dotfiles (like .rockbox) by default, you can select ANY folder or the drive root on your player. We will automatically find or create the .rockbox directory for you.",
        "fr": "Comme les systèmes d'exploitation (comme macOS) masquent les fichiers commençant par un point (comme .rockbox), vous pouvez sélectionner N'IMPORTE QUEL dossier ou la racine du lecteur. Nous localiserons ou créerons automatiquement le dossier .rockbox.",
        "es": "Dado que los sistemas operativos (como macOS) ocultan archivos con punto (como .rockbox) por defecto, puede seleccionar CUALQUIER carpeta o la raíz de la unidad. Localizaremos o crearemos automáticamente el directorio .rockbox por usted.",
    },
    "themepack_btn_select_folder": {
        "zh-CN": "选择播放器文件夹...",
        "en": "Select Folder on Player...",
        "fr": "Sélectionner un dossier sur le lecteur...",
        "es": "Seleccionar carpeta del reproductor...",
    },
    "themepack_status_downloading": {
        "zh-CN": "正在下载 360p 主题包...",
        "en": "Downloading 360p Theme Pack...",
        "fr": "Téléchargement du pack de thèmes 360p...",
        "es": "Descargando el paquete de temas 360p...",
    },
    "themepack_status_extracting": {
        "zh-CN": "正在将主题解压并合并至 .rockbox...",
        "en": "Installing themes into .rockbox...",
        "fr": "Installation des thèmes dans .rockbox...",
        "es": "Instalando temas en .rockbox...",
    },
    "themepack_complete": {
        "zh-CN": "主题包已安装成功！现在可以安全断开播放器，并在 Rockbox 中选择主题（设置 > 主题设置）。",
        "en": "Theme pack successfully installed! You can now safely disconnect your player and select a theme in Rockbox (Settings > Theme Settings).",
        "fr": "Pack de thèmes installé avec succès ! Vous pouvez déconnecter votre lecteur en toute sécurité et choisir un thème dans Rockbox (Paramètres > Paramètres du thème).",
        "es": "¡Paquete de temas instalado con éxito! Ahora puede desconectar con seguridad su reproductor y elegir un tema en Rockbox (Ajustes > Ajustes de tema).",
    },
    "themepack_err_not_found": {
        "zh-CN": "所选文件夹所在的驱动器上未能找到或创建 .rockbox 目录。请确保已选择正确的播放器驱动器。",
        "en": "Could not locate or initialize a .rockbox directory on the selected drive. Please ensure you selected a folder on your player.",
        "fr": "Impossible de trouver ou d'initialiser le dossier .rockbox sur le lecteur sélectionné. Assurez-vous d'avoir choisi un dossier de votre lecteur.",
        "es": "No se pudo encontrar o inicializar el directorio .rockbox en la unidad seleccionada. Asegúrese de haber seleccionado una carpeta de su reproductor.",
    },
    "rockbox_wizard_title": {
        "zh-CN": "Rockbox 360p 主题与字体安装向导",
        "en": "Rockbox 360p Themes & Fonts Setup",
        "fr": "Assistant de thèmes et polices Rockbox 360p",
        "es": "Asistente de temas y fuentes Rockbox 360p",
    },
    "rockbox_wizard_next": {
        "zh-CN": "继续",
        "en": "Continue",
        "fr": "Continuer",
        "es": "Continuar",
    },
    "rockbox_wizard_back": {
        "zh-CN": "返回",
        "en": "Back",
        "fr": "Retour",
        "es": "Atrás",
    },
    "rockbox_wizard_done": {
        "zh-CN": "完成",
        "en": "Done",
        "fr": "Terminé",
        "es": "Listo",
    },
    "rockbox_wizard_skip_adb": {
        "zh-CN": "跳过并使用 USB 存储模式",
        "en": "Skip to USB Storage Mode",
        "fr": "Passer au mode stockage USB",
        "es": "Pasar al modo de almacenamiento USB",
    },
    "rockbox_wizard_reboot_title": {
        "zh-CN": "第 1 步：重启播放器并重新连接",
        "en": "Step 1: Restart the player and reconnect",
        "fr": "Étape 1 : Redémarrez le lecteur et reconnectez-le",
        "es": "Paso 1: Reinicie el reproductor y vuelva a conectarlo",
    },
    "rockbox_wizard_reboot_desc": {
        "zh-CN": "1. 断开 USB 数据线。\n2. 长按电源键开机，进入 Rockbox 系统。\n3. 待 Rockbox 完全启动后，再用 USB 数据线连接电脑，然后点击“继续”。",
        "en": "1. Disconnect the USB cable.\n2. Power the player back on and let it boot into Rockbox.\n3. Once Rockbox is running, reconnect the USB cable, then press Continue.",
        "fr": "1. Débranchez le câble USB.\n2. Rallumez le lecteur et laissez Rockbox démarrer.\n3. Une fois Rockbox lancé, rebranchez le câble USB, puis appuyez sur Continuer.",
        "es": "1. Desconecte el cable USB.\n2. Encienda el reproductor y deje que arranque Rockbox.\n3. Cuando Rockbox esté en marcha, vuelva a conectar el cable USB y pulse Continuar.",
    },
    "rockbox_wizard_adb_title": {
        "zh-CN": "第 2 步：正在通过 ADB 查找播放器...",
        "en": "Step 2: Looking for the player over ADB...",
        "fr": "Étape 2 : Recherche du lecteur via ADB...",
        "es": "Paso 2: Buscando el reproductor por ADB...",
    },
    "rockbox_wizard_adb_desc": {
        "zh-CN": "应用正在检测 ADB 连接（最长 30 秒）。若找到设备，将自动传输主题包并重启播放器，无需手动操作。若未找到，可跳过并使用 USB 存储模式。",
        "en": "The app is watching for an ADB connection (up to 30 seconds). If a device is found, themes are transferred and the player rebooted automatically. Otherwise, skip ahead to USB Storage Mode.",
        "fr": "L'application surveille une connexion ADB (jusqu'à 30 secondes). Si un appareil est trouvé, les thèmes sont transférés et le lecteur redémarre automatiquement. Sinon, passez au mode stockage USB.",
        "es": "La aplicación está esperando una conexión ADB (hasta 30 segundos). Si se encuentra un dispositivo, los temas se transfieren y el reproductor se reinicia automáticamente. De lo contrario, pase al modo de almacenamiento USB.",
    },
    "rockbox_wizard_adb_searching": {
        "zh-CN": "正在等待 ADB 设备...（最长 30 秒）",
        "en": "Waiting for an ADB device... (up to 30 seconds)",
        "fr": "En attente d'un appareil ADB... (jusqu'à 30 secondes)",
        "es": "Esperando un dispositivo ADB... (hasta 30 segundos)",
    },
    "rockbox_wizard_adb_found": {
        "zh-CN": "已找到设备（{serial}），正在传输主题包...",
        "en": "Device found ({serial}); transferring themes...",
        "fr": "Appareil trouvé ({serial}) ; transfert des thèmes...",
        "es": "Dispositivo encontrado ({serial}); transfiriendo temas...",
    },
    "rockbox_wizard_adb_timeout": {
        "zh-CN": "30 秒内未检测到 ADB 设备，请使用 USB 存储模式继续。",
        "en": "No ADB device detected within 30 seconds; continuing with USB Storage Mode.",
        "fr": "Aucun appareil ADB détecté en 30 secondes ; passage au mode stockage USB.",
        "es": "No se detectó ningún dispositivo ADB en 30 segundos; continuando con el modo de almacenamiento USB.",
    },
    "rockbox_wizard_no_adb": {
        "zh-CN": "应用内未找到 ADB，将使用 USB 存储模式继续。",
        "en": "No ADB binary is available to the app; continuing with USB Storage Mode.",
        "fr": "Aucun binaire ADB disponible ; passage au mode stockage USB.",
        "es": "No hay binario ADB disponible; continuando con el modo de almacenamiento USB.",
    },
    "rockbox_wizard_adb_pushing": {
        "zh-CN": "正在通过 ADB 传输主题与字体...",
        "en": "Pushing themes and fonts over ADB...",
        "fr": "Envoi des thèmes et polices via ADB...",
        "es": "Enviando temas y fuentes por ADB...",
    },
    "rockbox_wizard_adb_push_failed": {
        "zh-CN": "ADB 传输失败，请改用 USB 存储模式",
        "en": "ADB transfer failed; falling back to USB Storage Mode",
        "fr": "Échec du transfert ADB ; bascule vers le mode stockage USB",
        "es": "Falló la transferencia ADB; usando el modo de almacenamiento USB",
    },
    "rockbox_wizard_usb_title": {
        "zh-CN": "通过 USB 存储模式安装",
        "en": "Install via USB Storage Mode",
        "fr": "Installation via le mode stockage USB",
        "es": "Instalación mediante el modo de almacenamiento USB",
    },
    "rockbox_wizard_usb_desc": {
        "zh-CN": "1. 在播放器上启用 USB 存储模式，使其在电脑上显示为驱动器。\n2. 点击下方按钮，选择播放器上的任意文件夹（.rockbox、Music、Themes 或磁盘根目录均可；隐藏的 .rockbox 文件夹会自动定位）。\n3. 应用将把 360p 主题与字体合并进去，同名文件会被覆盖。",
        "en": "1. Enable USB Storage Mode on the player so it appears as a drive.\n2. Pick ANY folder on the player below (.rockbox, Music, Themes, or the drive root — a hidden .rockbox folder is located automatically).\n3. The app merges the 360p themes and fonts in, overwriting same-name files.",
        "fr": "1. Activez le mode stockage USB sur le lecteur pour qu'il apparaisse comme un lecteur.\n2. Choisissez N'IMPORTE QUEL dossier ci-dessous (.rockbox, Musique, Thèmes ou la racine — un dossier .rockbox masqué est localisé automatiquement).\n3. L'application y fusionne les thèmes et polices 360p, en écrasant les fichiers de même nom.",
        "es": "1. Active el modo de almacenamiento USB en el reproductor para que aparezca como una unidad.\n2. Elija CUALQUIER carpeta de abajo (.rockbox, Música, Temas o la raíz — una carpeta .rockbox oculta se localiza automáticamente).\n3. La aplicación fusiona los temas y fuentes 360p, sobrescribiendo archivos del mismo nombre.",
    },
    "rockbox_wizard_done_title": {
        "zh-CN": "360p 主题与字体已安装！",
        "en": "360p themes & fonts installed!",
        "fr": "Thèmes et polices 360p installés !",
        "es": "¡Temas y fuentes 360p instalados!",
    },
    "rockbox_wizard_done_desc": {
        "zh-CN": "现在可以安全断开播放器，并在 Rockbox 中选择主题（设置 > 主题设置）。",
        "en": "You can now safely disconnect the player and pick a theme in Rockbox (Settings > Theme Settings).",
        "fr": "Vous pouvez déconnecter le lecteur en toute sécurité et choisir un thème dans Rockbox (Paramètres > Paramètres du thème).",
        "es": "Ahora puede desconectar el reproductor con seguridad y elegir un tema en Rockbox (Ajustes > Ajustes de tema).",
        "de": "Sie können das Gerät jetzt sicher trennen und ein Theme in Rockbox auswählen (Einstellungen > Theme-Einstellungen).",
        "ja": "安全にプレイヤーを取り外して、Rockbox でテーマを選択できます（設定 > テーマ設定）。",
    },
    "sel_local_rockbox_themes_option": {
        "en": "Install Rockbox 360p Themes + Fontpack",
        "zh-CN": "安装 Rockbox 360p 主题与字体包",
        "de": "Rockbox 360p Themes + Schriftartenpaket installieren",
        "fr": "Installer les thèmes Rockbox 360p et le pack de polices",
        "es": "Instalar temas y fuentes Rockbox 360p",
        "ja": "Rockbox 360p テーマとフォントパックをインストール",
    },
    "rockbox_stage_installing": {
        "en": "Installing Rockbox Themes & Fonts...",
        "zh-CN": "正在安装 Rockbox 主题与字体…",
        "de": "Rockbox-Designs und Schriftarten werden installiert…",
        "fr": "Installation des thèmes et polices Rockbox…",
        "es": "Instalando temas y fuentes de Rockbox...",
        "ja": "Rockbox のテーマとフォントをインストール中…",
    },
    "rockbox_stage_waiting_adb_fmt": {
        "en": "Looking for player via ADB ({remaining}s)...",
        "zh-CN": "正在通过 ADB 检测播放器（{remaining}秒）…",
        "de": "Suche nach Player über ADB ({remaining}s)…",
        "fr": "Recherche de l'appareil via ADB ({remaining}s)…",
        "es": "Buscando reproductor mediante ADB ({remaining}s)...",
        "ja": "ADB 経由でプレイヤーを検出中（残り {remaining} 秒）…",
    },
    "rockbox_stage_pushing": {
        "en": "Player detected. Placing community themes & fonts...",
        "zh-CN": "已检测到播放器，正在放置社区主题与字体…",
        "de": "Player erkannt. Community-Themes & Schriftarten werden übertragen…",
        "fr": "Appareil détecté. Transfert des thèmes et polices de la communauté…",
        "es": "Reproductor detectado. Transfiriendo temas y fuentes comunitarias...",
        "ja": "プレイヤーを検出しました。コミュニティテーマとフォントを転送中…",
    },
    "rockbox_stage_pushing_files_fmt": {
        "en": "Placing theme files ({curr} of {total})...",
        "zh-CN": "正在放置主题文件（{curr} / {total}）…",
        "de": "Theme-Dateien werden übertragen ({curr} von {total})…",
        "fr": "Transfert des fichiers de thèmes ({curr} sur {total})…",
        "es": "Transfiriendo archivos de temas ({curr} de {total})...",
        "ja": "テーマファイルを転送中（{curr} / {total}）…",
    },
    "rockbox_wizard_reboot_desc_y1": {
        "en": "Hold the centre button to power on your Y1, reconnect USB, and select USB Storage Mode.",
        "en-US": "Hold the center button to power on your Y1, reconnect USB, and select USB Storage Mode.",
        "zh-CN": "请长按中心按键开启 Y1，重新连接 USB，并在播放器上选择“USB 存储模式”。",
        "de": "Halten Sie die mittlere Taste gedrückt, um das Y1 einzuschalten, verbinden Sie USB erneut und wählen Sie den USB-Speichermodus.",
        "fr": "Maintenez le bouton central pour allumer votre Y1, reconnectez l'USB et sélectionnez le mode Stockage USB.",
        "es": "Mantén presionado el botón central para encender tu Y1, vuelve a conectar el USB y selecciona el Modo de Almacenamiento USB.",
        "ja": "中央ボタンを長押しして Y1 を起動し、USB を再接続して「USB ストレージモード」を選択してください。",
    },
    "rockbox_wizard_reboot_desc_power": {
        "en": "Hold the power button to power on your player, reconnect USB, and select USB Storage Mode.",
        "zh-CN": "请长按电源键开启播放器，重新连接 USB，并在播放器上选择“USB 存储模式”。",
        "de": "Halten Sie die Ein-/Aus-Taste gedrückt, verbinden Sie USB erneut und wählen Sie den USB-Speichermodus.",
        "fr": "Maintenez le bouton d'alimentation pour allumer l'appareil, reconnectez l'USB et sélectionnez le mode Stockage USB.",
        "es": "Mantén presionado el botón de encendido para encender el reproductor, vuelve a conectar el USB y selecciona el Modo de Almacenamiento USB.",
        "ja": "電源ボタンを長押しして起動し、USB を再接続して「USB ストレージモード」を選択してください。",
    },
    "rockbox_recover_troubleshoot": {
        "en": "Troubleshooting: Restore original software",
        "zh-CN": "故障排除：恢复原厂官方软件",
        "de": "Problembehebung: Original-Software wiederherstellen",
        "fr": "Dépannage : Restaurer le logiciel d'origine",
        "es": "Solución de problemas: Restaurar el software original",
        "ja": "トラブルシューティング：元の公式ソフトウェアを復元",
    },
    "rockbox_step1_disconnect_power_on": {
        "en": "Please disconnect and power on your {model}, then press Next",
        "zh-CN": "请断开 USB 并开启您的 {model}，然后点击下一步",
        "de": "Bitte trennen Sie Ihr {model}, schalten Sie es ein und klicken Sie auf Weiter",
        "fr": "Veuillez débrancher et allumer votre {model}, puis cliquer sur Suivant",
        "es": "Desconecta y enciende tu {model}, luego haz clic en Siguiente",
        "ja": "{model} を取り外して電源を入れ、次へをクリックしてください",
    },
    "rockbox_step1_power_on_button": {
        "en": "Disconnect USB, hold the {button} to power on your {model}, then press Next.",
        "zh-CN": "断开 USB，长按{button}开启 {model}，然后点击下一步。",
        "de": "Trennen Sie USB, halten Sie die {button} gedrückt, um Ihr {model} einzuschalten, und klicken Sie dann auf Weiter.",
        "fr": "Débranchez l'USB, maintenez le {button} enfoncé pour allumer votre {model}, puis cliquez sur Suivant.",
        "es": "Desconecta el USB, mantén pulsado el {button} para encender tu {model} y pulsa Siguiente.",
        "ja": "USB を取り外し、{button}を長押しして {model} の電源を入れてから「次へ」を押してください。",
    },
    "rockbox_step2_connect_usb": {
        "en": "Please connect your {model} by USB",
        "zh-CN": "请使用 USB 连接您的 {model}",
        "de": "Bitte verbinden Sie Ihr {model} per USB",
        "fr": "Veuillez connecter votre {model} par USB",
        "es": "Conecta tu {model} por USB",
        "ja": "{model} を USB で接続してください",
    },
    "rockbox_step_usb_storage_mode": {
        "en": "Please enable USB storage mode on your device and click Next",
        "zh-CN": "请在设备上开启 USB 存储模式，然后点击下一步",
        "de": "Bitte aktivieren Sie den USB-Speichermodus auf Ihrem Gerät und klicken Sie auf Weiter",
        "fr": "Veuillez activer le mode stockage USB sur votre appareil et cliquer sur Suivant",
        "es": "Activa el modo de almacenamiento USB en tu dispositivo y haz clic en Siguiente",
        "ja": "デバイスで USB ストレージモードを有効にして、次へをクリックしてください",
    },
    "rockbox_step_select_player_folder": {
        "en": "Please select the player itself or one of the folders on it",
        "zh-CN": "请选择播放器磁盘本身或其上的任一文件夹",
        "de": "Bitte wählen Sie den Player selbst oder einen Ordner darauf aus",
        "fr": "Veuillez sélectionner le lecteur lui-même ou l'un de ses dossiers",
        "es": "Selecciona el reproductor o una de las carpetas dentro de él",
        "ja": "プレイヤー本体またはその中のいずれかのフォルダーを選択してください",
    },
    "rockbox_power_on_prompt_sp_y1": {
        "en": "Disconnect the USB and power on your Y1, then press Next.",
        "en-US": "Disconnect the USB and power on your Y1, then press Next.",
        "zh-CN": "请按住 Y1 的中心按键不放，然后点击“下一步”。Updater 将开始通过 ADB 等待您的 Y1。",
        "de": "Halten Sie die mittlere Taste am Y1 gedrückt, und drücken Sie dann „Weiter“. Updater sucht danach das Y1 über ADB.",
        "fr": "Maintenez le bouton central de votre Y1 enfoncé, puis appuyez sur Suivant. Updater surveillera alors votre Y1 via ADB.",
        "es": "Mantén presionado el botón central de tu Y1 y luego pulsa Siguiente. Updater buscará entonces tu Y1 por ADB.",
        "ja": "Y1 の中央ボタンを押し続けたまま「次へ」を押してください。その後、ADB 経由で Y1 を探します。",
    },
    "rockbox_power_on_prompt_sp_other": {
        "en": "Disconnect the USB and power on your {model}, then press Next.",
        "zh-CN": "请按住 {model} 的电源键不放，然后点击“下一步”。Updater 将开始通过 ADB 等待您的 {model}。",
        "de": "Halten Sie die Ein-/Aus-Taste an Ihrem {model} gedrückt, und drücken Sie dann „Weiter“. Updater sucht danach Ihr {model} über ADB.",
        "fr": "Maintenez le bouton d'alimentation de votre {model} enfoncé, puis appuyez sur Suivant. Updater surveillera alors votre {model} via ADB.",
        "es": "Mantén presionado el botón de encendido de tu {model} y luego pulsa Siguiente. Updater buscará entonces tu {model} por ADB.",
        "ja": "{model} の電源ボタンを押し続けたまま「次へ」を押してください。その後、ADB 経由で {model} を探します。",
    },
    "rockbox_ask_running_fmt": {
        "en": "Is Rockbox running on your {model}?",
        "zh-CN": "Rockbox 是否已在您的 {model} 上正常运行？",
        "de": "Läuft Rockbox auf Ihrem {model}?",
        "fr": "Est-ce que Rockbox fonctionne sur votre {model} ?",
        "es": "¿Está funcionando Rockbox en tu {model}?",
        "ja": "{model}で Rockbox は起動していますか？",
    },
    "rockbox_running_yes": {
        "en": "Yes, Rockbox is running",
        "zh-CN": "是的，Rockbox 已运行",
        "de": "Ja, Rockbox läuft",
        "fr": "Oui, Rockbox fonctionne",
        "es": "Sí, Rockbox está funcionando",
        "ja": "はい、Rockbox が起動しています",
    },
    "rockbox_running_no": {
        "en": "No / Won't start",
        "zh-CN": "否 / 无法启动",
        "de": "Nein / Startet nicht",
        "fr": "Non / Ne démarre pas",
        "es": "No / No arranca",
        "ja": "いいえ / 起動しません",
    },
    "rockbox_manual_guide": {
        "en": "1. Select 'USB Storage Mode' on your player.\n2. Click 'Browse Folder...' to select your player and install themes.",
        "zh-CN": "1. 在播放器屏幕上选择“USB 存储模式”。\n2. 点击“浏览文件夹...”选择播放器磁盘以安装主题与字体。",
        "de": "1. Wählen Sie 'USB-Speichermodus' auf Ihrem Player.\n2. Klicken Sie auf 'Ordner durchsuchen...', um Ihren Player auszuwählen und Themes zu installieren.",
        "fr": "1. Sélectionnez le « Mode stockage USB » sur votre lecteur.\n2. Cliquez sur « Parcourir le dossier… » pour sélectionner votre lecteur et installer les thèmes.",
        "es": "1. Selecciona 'Modo de almacenamiento USB' en tu reproductor.\n2. Haz clic en 'Examinar carpeta...' para seleccionar tu reproductor e instalar temas.",
        "ja": "1. プレイヤー画面で「USBストレージモード」を選択してください。\n2. 「フォルダーを参照...」をクリックしてプレイヤーを選択し、テーマをインストールしてください。",
    },
    "rockbox_btn_finish": {
        "en": "Finish",
        "zh-CN": "完成",
        "de": "Fertigstellen",
        "fr": "Terminer",
        "es": "Finalizar",
        "ja": "完了",
    },
    "rockbox_site_themes_note": {
        "en": "This Rockbox build uses 240\u00d7320 themes, which are not bundled. Get free themes for your player from the Rockbox theme site:",
        "zh-CN": "\u6b64 Rockbox \u7248\u672c\u4f7f\u7528 240\u00d7320 \u4e3b\u9898\uff0c\u672a\u968f\u5e94\u7528\u6253\u5305\u3002\u8bf7\u4ece Rockbox \u4e3b\u9898\u7f51\u7ad9\u4e3a\u60a8\u7684\u64ad\u653e\u5668\u83b7\u53d6\u514d\u8d39\u4e3b\u9898\uff1a",
        "de": "Dieser Rockbox-Build verwendet 240\u00d7320-Themes, die nicht mitgeliefert werden. Hole dir kostenlose Themes f\u00fcr deinen Player von der Rockbox-Theme-Seite:",
        "fr": "Cette version de Rockbox utilise des th\u00e8mes 240\u00d7320, non inclus. T\u00e9l\u00e9chargez des th\u00e8mes gratuits pour votre lecteur sur le site de th\u00e8mes Rockbox :",
        "es": "Esta compilaci\u00f3n de Rockbox usa temas de 240\u00d7320, no incluidos. Consigue temas gratuitos para tu reproductor en el sitio de temas de Rockbox:",
        "ja": "\u3053\u306e Rockbox \u30d3\u30eb\u30c9\u306f\u540c\u68b1\u3055\u308c\u3066\u3044\u306a\u3044 240\u00d7320 \u30c6\u30fc\u30de\u3092\u4f7f\u7528\u3057\u307e\u3059\u3002Rockbox \u30c6\u30fc\u30de\u30b5\u30a4\u30c8\u304b\u3089\u7121\u6599\u30c6\u30fc\u30de\u3092\u5165\u624b\u3067\u304d\u307e\u3059\uff1a",
    },
    "rockbox_site_themes_open": {
        "en": "Open the Rockbox theme site",
        "zh-CN": "\u6253\u5f00 Rockbox \u4e3b\u9898\u7f51\u7ad9",
        "de": "Rockbox-Theme-Seite \u00f6ffnen",
        "fr": "Ouvrir le site de th\u00e8mes Rockbox",
        "es": "Abrir el sitio de temas de Rockbox",
        "ja": "Rockbox \u30c6\u30fc\u30de\u30b5\u30a4\u30c8\u3092\u958b\u304f",
    },
    "rockbox_troubleshoot_desc": {
        "en": "If your {model} does not boot, disconnect USB, hold the power button for 10s to reset, and power on. Or restore original software.",
        "zh-CN": "如果您的 {model} 无法开机，请断开 USB，长按电源键 10 秒复位后重新开机，或选择恢复原厂固件。",
        "de": "Wenn Ihr {model} nicht startet, trennen Sie USB, halten Sie die Taste 10s zum Zurücksetzen gedrückt und schalten Sie ein. Oder stellen Sie die Originalsoftware wieder her.",
        "fr": "Si votre {model} ne démarre pas, débranchez l'USB, maintenez le bouton d'alimentation 10s pour réinitialiser, puis allumez. Ou restaurez le logiciel d'origine.",
        "es": "Si tu {model} no arranca, desconecta el USB, mantén presionado el botón de encendido 10s para reiniciar y enciende. O restaura el software original.",
        "ja": "{model}が起動しない場合は、USBを抜き、電源ボタンを10秒長押ししてリセット後に再起動するか、元のソフトウェアを復元してください。",
    },
    "err_usb_lost_title": {
        "en": "The USB connection was lost.",
        "zh-CN": "USB 连接已断开。",
        "de": "Die USB-Verbindung wurde getrennt.",
        "fr": "La connexion USB a été perdue.",
        "es": "Se perdió la conexión USB.",
        "ja": "USB 接続が切断されました。",
    },
    "err_usb_lost_subtitle": {
        "en": "Would you like to try again?",
        "zh-CN": "是否重新尝试？",
        "de": "Möchten Sie es erneut versuchen?",
        "fr": "Souhaitez-vous réessayer ?",
        "es": "¿Deseas intentarlo de nuevo?",
        "ja": "もう一度お試しになりますか？",
    },
    "action_writing_file_status": {
        "en": "Writing {image} ({sent} of {total})",
        "zh-CN": "正在写入 {image}（{sent} / {total}）",
        "de": "Schreiben von {image} ({sent} von {total})",
        "fr": "Écriture de {image} ({sent} sur {total})",
        "es": "Escribiendo {image} ({sent} de {total})",
        "ja": "{image} を書き込み中（{sent} / {total}）",
    },
    "legacy_cleanup_title": {
        "zh-CN": "移除旧版应用",
        "en": "Remove old app",
        "fr": "Supprimer l'ancienne app",
        "es": "Eliminar la app anterior",
    },
    "legacy_cleanup_desc": {
        "zh-CN": "此电脑上仍存有旧版的 Updater，占用磁盘空间。卸载后即可释放。",
        "en": "An older version of Updater is still on this computer and takes up disk space. Uninstalling it frees that space.",
        "fr": "Une ancienne version d'Updater est encore sur cet ordinateur et occupe de l'espace disque. La désinstaller libère cet espace.",
        "es": "Todavía hay una versión anterior de Updater en este equipo y ocupa espacio en disco. Desinstalarla libera ese espacio.",
    },
    "legacy_cleanup_reclaim": {
        "zh-CN": "可释放约 {size} 磁盘空间",
        "en": "Reclaims approx {size} of disk space",
        "fr": "Libère environ {size} d'espace disque",
        "es": "Libera aprox {size} de espacio en disco",
    },
    "legacy_cleanup_remove_app_cb": {
        "zh-CN": "移除旧版 Innioasis Updater 应用程序及支持目录",
        "en": "Remove older Innioasis Updater application and support folders",
        "fr": "Supprimer l'ancienne application Innioasis Updater et ses dossiers",
        "es": "Eliminar la aplicación Innioasis Updater anterior y carpetas de soporte",
    },
    "legacy_cleanup_keep_platform_tools_cb": {
        "zh-CN": "保留已安装的 Android Platform Tools (ADB / Fastboot)",
        "en": "Keep Android Platform Tools (ADB / Fastboot) installed",
        "fr": "Conserver les outils Android Platform Tools (ADB / Fastboot)",
        "es": "Mantener instaladas las herramientas de plataforma Android (ADB / Fastboot)",
    },
    "legacy_cleanup_platform_tools_hint": {
        "zh-CN": "如果您在 更新程序 之外不需要使用 ADB 或 Fastboot，可取消勾选以卸载并释放更多空间。",
        "en": "If you do not use ADB or Fastboot outside Updater CE, uncheck to uninstall and reclaim additional storage.",
        "fr": "Si vous n'utilisez pas ADB ou Fastboot en dehors d'Programme de mise à jour, décochez pour désinstaller et libérer de l'espace.",
        "es": "Si no utiliza ADB o Fastboot fuera de Actualizador, desmárquelo para desinstalar y liberar espacio.",
    },
    "legacy_cleanup_btn_clean": {
        "zh-CN": "卸载",
        "en": "Uninstall",
        "fr": "Désinstaller",
        "es": "Desinstalar",
    },
    "legacy_cleanup_btn_later": {
        "zh-CN": "暂不处理",
        "en": "Not Now",
        "fr": "Plus tard",
        "es": "Ahora no",
    },
    "legacy_cleanup_done_title": {
        "zh-CN": "清理完成",
        "en": "Clean Up Complete",
        "fr": "Nettoyage terminé",
        "es": "Limpieza completada",
    },
    "legacy_cleanup_done_msg": {
        "zh-CN": "已成功清理旧版安装并释放磁盘空间。",
        "en": "Successfully cleaned up legacy installation and reclaimed disk space.",
        "fr": "L'ancienne installation a été nettoyée avec succès.",
        "es": "Se limpió con éxito la instalación anterior.",
    },
    "legacy_cleanup_card_title": {
        "zh-CN": "移除旧版应用",
        "en": "Remove old app",
        "fr": "Supprimer l'ancienne app",
        "es": "Eliminar la app anterior",
    },
    "legacy_cleanup_card_desc": {
        "zh-CN": "旧版本的 Updater 仍在此电脑上。卸载它可以释放磁盘空间。",
        "en": "An older version of Updater is still on this computer. Uninstalling it frees up disk space.",
        "fr": "Une ancienne version d'Updater est encore sur cet ordinateur. La désinstaller libère de l'espace disque.",
        "es": "Todavía hay una versión anterior de Updater en este equipo. Desinstalarla libera espacio en disco.",
    },
    "legacy_cleanup_scan_btn": {
        "zh-CN": "卸载",
        "en": "Uninstall",
        "fr": "Désinstaller",
        "es": "Desinstalar",
    },
    "legacy_cleanup_none_found": {
        "zh-CN": "未找到旧版应用。",
        "en": "No old version was found.",
        "fr": "Aucune ancienne version n'a été trouvée.",
        "es": "No se encontró ninguna versión anterior.",
    },
    "legacy_cleanup_confirm_body": {
        "zh-CN": "这会移除旧版应用及其支持文件。下载的固件会保留。",
        "en": "This removes the old app and its support files. Downloaded firmware is kept.",
        "fr": "Cela supprime l'ancienne app et ses fichiers de support. Les firmwares téléchargés sont conservés.",
        "es": "Esto elimina la app anterior y sus archivos de soporte. El firmware descargado se mantiene.",
    },
    "legacy_cleanup_tools_body": {
        "zh-CN": "是否同时移除 Android Platform Tools (ADB / Fastboot)？如果使用 ADB，请选择“保留”。",
        "en": "Remove the Android Platform Tools (ADB / Fastboot) as well? Choose Keep if you use ADB.",
        "fr": "Supprimer aussi les outils Android Platform Tools (ADB / Fastboot) ? Choisissez Conserver si vous utilisez ADB.",
        "es": "¿Eliminar también las herramientas de plataforma Android (ADB / Fastboot)? Elija Conservar si usa ADB.",
    },
    "legacy_cleanup_keep_btn": {
        "zh-CN": "保留",
        "en": "Keep",
        "fr": "Conserver",
        "es": "Conservar",
    },
    "legacy_cleanup_remove_btn": {
        "zh-CN": "移除",
        "en": "Remove",
        "fr": "Supprimer",
        "es": "Eliminar",
    },
    "legacy_cleanup_done_inline": {
        "zh-CN": "已移除，释放约 {size}。",
        "en": "Removed — about {size} freed.",
        "fr": "Supprimé — environ {size} libérés.",
        "es": "Eliminado: se liberaron unos {size}.",
    },
}


# ---------------------------------------------------------------------------
# Language catalogs (assets/i18n/<code>.json)
# ---------------------------------------------------------------------------
_LANGUAGES: dict = {}
_CATALOGS: dict = {}

# Language code -> the country whose flag stands for it. Google's list is
# language-based, so a representative country is chosen where the code carries
# no region of its own; a code like pt-BR, fr-CA or zh-TW already names one.
# English carries no region on purpose: the international table claims no
# country, while en-GB and en-US get their own flag from the code's suffix.
_FLAG_REGIONS = {
    "af": "ZA", "am": "ET", "ar": "SA", "as": "IN", "az": "AZ", "be": "BY",
    "bg": "BG", "bho": "IN", "bm": "ML", "bn": "BD", "bs": "BA", "ca": "ES",
    "ceb": "PH", "co": "FR", "cs": "CZ", "cy": "GB", "da": "DK", "de": "DE",
    "doi": "IN", "dv": "MV", "ee": "GH", "el": "GR", "eo": "EU",
    "es": "ES", "et": "EE", "eu": "ES", "fa": "IR", "fi": "FI", "fil": "PH",
    "fr": "FR", "fy": "NL", "ga": "IE", "gd": "GB", "gl": "ES", "gn": "PY",
    "gu": "IN", "ha": "NG", "haw": "US", "he": "IL", "hi": "IN", "hmn": "LA",
    "hr": "HR", "ht": "HT", "hu": "HU", "hy": "AM", "id": "ID", "ig": "NG",
    "ilo": "PH", "is": "IS", "it": "IT", "ja": "JP", "jv": "ID", "ka": "GE",
    "kk": "KZ", "km": "KH", "kn": "IN", "ko": "KR", "kri": "SL", "ku": "TR",
    "ky": "KG", "la": "VA", "lb": "LU", "lg": "UG", "ln": "CD", "lo": "LA",
    "lt": "LT", "lus": "IN", "lv": "LV", "mai": "IN", "mg": "MG", "mi": "NZ",
    "mk": "MK", "ml": "IN", "mn": "MN", "mni": "IN", "mr": "IN", "ms": "MY",
    "mt": "MT", "my": "MM", "ne": "NP", "nl": "NL", "no": "NO", "nso": "ZA",
    "ny": "MW", "om": "ET", "or": "IN", "pa": "IN", "pl": "PL", "ps": "AF",
    "pt": "PT", "qu": "PE", "ro": "RO", "ru": "RU", "rw": "RW", "sa": "IN",
    "sd": "PK", "si": "LK", "sk": "SK", "sl": "SI", "sm": "WS", "sn": "ZW",
    "so": "SO", "sq": "AL", "sr": "RS", "st": "ZA", "su": "ID", "sv": "SE",
    "sw": "KE", "ta": "IN", "te": "IN", "tg": "TJ", "th": "TH", "ti": "ET",
    "tk": "TM", "tr": "TR", "ts": "ZA", "tt": "RU", "ug": "CN", "uk": "UA",
    "ur": "PK", "uz": "UZ", "vi": "VN", "xh": "ZA", "yi": "IL", "yo": "NG",
    "zh": "CN", "zu": "ZA",
}


def catalog_dir() -> Path:
    """Where the language catalogs live, in a checkout or in the bundle."""
    try:
        from . import paths

        candidates = [paths.RESOURCES_DIR / "i18n", paths.REPO_ROOT / "assets" / "i18n"]
    except Exception:  # paths is optional when i18n is used standalone
        candidates = [Path(__file__).resolve().parent.parent / "assets" / "i18n"]
    for candidate in candidates:
        if (candidate / "languages.json").is_file():
            return candidate
    return candidates[0]


def languages() -> dict:
    """``{code: {"native": ..., "english": ...}}`` as shipped in languages.json."""
    if "table" not in _LANGUAGES:
        data = {}
        try:
            raw = json.loads(
                (catalog_dir() / "languages.json").read_text(encoding="utf-8")
            )
            data = {
                str(code): entry
                for code, entry in (raw or {}).items()
                if isinstance(entry, dict)
            }
        except Exception:
            logger.debug("Language list unavailable", exc_info=True)
        _LANGUAGES["table"] = data
    return _LANGUAGES["table"]


def catalog_codes() -> tuple:
    """Every language the app can be set to, curated ones first."""
    found = [code for code in _SUPPORTED]
    directory = catalog_dir()
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            code = path.stem
            if code != "languages" and code not in found:
                found.append(code)
    return tuple(found)


def catalog(code: str) -> dict:
    """The ``{string_id: text}`` catalog for ``code`` (empty when missing)."""
    code = (code or "").strip()
    if code in _CATALOGS:
        return _CATALOGS[code]
    table: dict = {}
    path = catalog_dir() / f"{code}.json"
    if code and path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            table = {
                str(key): value
                for key, value in (raw or {}).items()
                if isinstance(value, str) and value.strip()
            }
        except Exception:
            logger.debug("Catalog %s unreadable", code, exc_info=True)
    _CATALOGS[code] = table
    return table


def language_label(code: str) -> str:
    """The language's own name, then its English name, then the code."""
    entry = languages().get(code) or {}
    return entry.get("native") or entry.get("english") or code


def curated_codes() -> tuple:
    """The languages the app is hand-translated into, in picker order."""
    found = set(catalog_codes()) | set(_SUPPORTED)
    return tuple(code for code in _SUPPORTED if code in found)


def language_choices() -> list:
    """``[(code, native, english)]`` for the picker, in native-name order."""
    rows = []
    for code in catalog_codes():
        entry = languages().get(code) or {}
        native = entry.get("native") or entry.get("english") or code
        english = entry.get("english") or native
        rows.append((code, native, english))
    rows.sort(key=lambda row: row[1].casefold())
    return rows


def flag_supported() -> bool:
    """Whether this platform can draw flag emoji.

    macOS and the usual Linux desktops ship a colour emoji font with regional
    indicators. Windows' Segoe UI Emoji does not, and a flag there renders as
    two letters — so no flag is offered at all rather than a broken glyph.
    ``UPDATER_LANGUAGE_FLAGS`` overrides the guess.
    """
    override = os.environ.get("UPDATER_LANGUAGE_FLAGS")
    if override is not None and override.strip() != "":
        return override.strip().lower() not in ("0", "false", "no", "off")
    return sys.platform == "darwin" or sys.platform.startswith("linux")


def flag(code: str) -> str:
    """A flag emoji for a language code, or ``""`` when there is no good one."""
    if not flag_supported():
        return ""
    region = ""
    parts = (code or "").split("-")
    if len(parts) >= 2 and len(parts[-1]) == 2 and parts[-1].isalpha():
        region = parts[-1].upper()
    if not region:
        region = _FLAG_REGIONS.get(code) or _FLAG_REGIONS.get(parts[0], "")
    if len(region) != 2 or not region.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in region.upper())


def language_for_locale(name: str) -> str:
    """Map a locale name such as ``fr_CA`` onto a language the app can show."""
    raw = (name or "").replace("_", "-").strip()
    available = set(catalog_codes()) | set(languages())
    if not raw:
        return "en"
    # Chinese is the one language where the region decides the table: the app
    # ships Simplified, so any zh-* locale that has no table of its own lands
    # on zh-CN rather than on English.
    if raw.lower().startswith("zh"):
        for candidate in ("zh-CN" if raw.lower() in ("zh", "zh-hans") or "cn" in raw.lower() or "sg" in raw.lower() else "zh-TW", "zh-CN"):
            if candidate in available:
                return candidate
    candidates = [raw] + _locale_variants(raw)
    lowered = {code.lower(): code for code in available}
    for candidate in candidates:
        if candidate in available:
            return candidate
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return "en"


def _locale_variants(raw: str) -> list:
    """Progressively looser matches for a locale name."""
    out = []
    parts = raw.split("-")
    if len(parts) > 1:
        out.append(parts[0])
    # pt-BR has its own table; plain pt, de-AT and the like fall back to the
    # base language, then to the closest table sharing that base.
    out.append(raw.replace("-", "_"))
    return out


def system_language() -> str:
    """The language to use when the user has never chosen one."""
    try:
        from PySide6.QtCore import QLocale

        name = QLocale.system().name() or ""
    except Exception:
        name = ""
    return language_for_locale(name)


# Languages written right to left. The interface mirrors for these: the
# sidebar moves to the right and controls align to the right, while images are
# shown as-is (repositioned, never flipped).
RTL_LANGUAGES = frozenset({
    "ar", "arc", "ckb", "dv", "fa", "fa-AF", "he", "iw", "ks", "ku-Arab",
    "nqo", "ps", "sd", "syr", "ug", "ur", "yi",
})


def is_rtl_language(code: str | None = None) -> bool:
    """True when ``code`` (or the active language) reads right to left."""
    lang = (code if code is not None else translator().lang) or ""
    lang = lang.strip()
    if not lang:
        return False
    return lang in RTL_LANGUAGES or lang.split("-")[0] in RTL_LANGUAGES


def is_english_language(code: str | None = None) -> bool:
    """True when ``code`` (or the active language) is English of any region.

    ``en``, ``en-GB`` and ``en-US`` all show English copy, so the places that
    translate text — release notes in particular — have to treat a regional
    variant as English too instead of asking Google to translate English into
    English.
    """
    lang = (code if code is not None else translator().lang) or ""
    return lang.strip().split("-")[0].lower() == "en"


def initial_language(stored: str = "") -> str:
    """The user's chosen language, or else the system's.

    A stored value only wins when it is a language the app still has. Nothing
    is stored for a detected language, so the app keeps following the system
    until the user picks one in the app.
    """
    chosen = (stored or "").strip()
    if chosen and chosen in set(catalog_codes()) | set(_SUPPORTED) | set(languages()):
        return chosen
    return system_language()


_FIELD_CACHE: dict = {}


def _format_fields(text: str) -> frozenset:
    """``{placeholder}`` names a string expects, for comparison across languages.

    ``str.format`` raises ``KeyError`` when the text asks for a field the call
    site did not pass, so the names — not the wording — have to survive
    translation.
    """
    cached = _FIELD_CACHE.get(text)
    if cached is not None:
        return cached
    names = set()
    try:
        for _literal, field, _spec, _conversion in string.Formatter().parse(text):
            if field:
                names.add(field.split(".")[0].split("[")[0])
    except Exception:
        names = set()
    fields = frozenset(names)
    if len(_FIELD_CACHE) < 8192:
        _FIELD_CACHE[text] = fields
    return fields


def placeholder_safe_text(key: str, text: str) -> str:
    """``text``, or its English original when a translation renamed a field.

    The bundled catalogs are machine-translated, and a hundred of them turned
    ``{day}`` into their own word for it (``{devezh}``, ``{nino}``). Every
    ``tr(...).format(...)`` call site then raised ``KeyError`` — the supporters
    ticker and the install status lines among them. A translation may use fewer
    placeholders than the source (the argument is simply unused); it may not
    invent one, so those fall back to the English string rather than crash.
    """
    source = catalog("en").get(key) or (_STRINGS.get(key) or {}).get("en") or ""
    if not source or not text:
        return text
    invented = _format_fields(text) - _format_fields(source)
    if not invented:
        return text
    logger.debug(
        "Catalog translation for %r renamed placeholders %s; using English",
        key,
        sorted(invented),
    )
    return source


class _Translator:
    def __init__(self):
        self._lang = "en"

    @property
    def lang(self) -> str:
        return self._lang

    def set_language(self, lang: str):
        code = (lang or "").strip()
        if code and (code in catalog_codes() or code in _SUPPORTED):
            self._lang = code

    def t(self, key: str) -> str:
        table = _STRINGS.get(key)
        if not table:
            return key
        translated = catalog(self._lang).get(key)
        if translated:
            return placeholder_safe_text(key, translated)
        english_catalog = catalog("en")
        return table.get(self._lang) or english_catalog.get(key) or table.get("en") or key


_global_translator = _Translator()


def translator() -> _Translator:
    """Module-level translator (the app installs one global instance)."""
    return _global_translator


def tr(key: str) -> str:
    return _global_translator.t(key)


def current_language() -> str:
    """The active UI language code (``en`` unless the user chose another)."""
    return _global_translator.lang


def tr_brand(key: str) -> str:
    """Translate ``key``, preferring its ``_mediatek`` variant in that build.

    The MediaTek Installer must not name Updater CE, the Community Firmware
    Archive or the Themes Gallery, so branded copy is looked up by variant.
    """
    from .config import is_mediatek_installer

    if is_mediatek_installer() and f"{key}_mediatek" in _STRINGS:
        return tr(f"{key}_mediatek")
    return tr(key)
