"""Internationalisation module.

Structure mirrors InniUpdaterChin's ``app.i18n`` (a ``_Translator`` with a
``t(key)`` lookup). The original app ships six languages (zh-CN, en, ja, de,
fr, es); this port ships Chinese, English, French and Spanish for every string
it uses. The remaining tables (ja, de) can be mined from
``app/i18n.pyc`` in the InniUpdaterChin install and dropped into ``_STRINGS``.
"""

_SUPPORTED = ("zh-CN", "en", "fr", "es")

_STRINGS: dict = {
    "app_name": {
        "zh-CN": "因尼奥斯升级工具",
        "en": "Innioasis Updater",
        "fr": "Innioasis Updater",
        "es": "Innioasis Updater",
    },
    "app_title": {
        "zh-CN": "刷机升级",
        "en": "Flash Upgrade",
        "fr": "Mise à jour du firmware",
        "es": "Actualización de firmware",
    },

    # Navigation
    "nav_home": {"zh-CN": "首页", "en": "Home", "fr": "Accueil", "es": "Inicio"},
    "nav_select_package": {
        "zh-CN": "选择软件包",
        "en": "Select Package",
        "fr": "Choisir un paquet",
        "es": "Seleccionar paquete",
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
    },
    "nav_language": {
        "zh-CN": "语言",
        "en": "Language",
        "fr": "Langue",
        "es": "Idioma",
    },
    "nav_contact": {
        "zh-CN": "联系我们",
        "en": "Contact Us",
        "fr": "Nous contacter",
        "es": "Contáctanos",
    },
    "nav_donate": {
        "zh-CN": "支持我们",
        "en": "Support Us",
        "fr": "Nous soutenir",
        "es": "Apóyanos",
    },
    "nav_check_updates": {
        "zh-CN": "检查更新",
        "en": "Check for Updates",
        "fr": "Vérifier les mises à jour",
        "es": "Buscar actualizaciones",
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
        "zh-CN": "选择软件包",
        "en": "Select Package",
        "fr": "Choisir un paquet",
        "es": "Seleccionar paquete",
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
    "sel_model": {
        "zh-CN": "设备型号",
        "en": "Device Model",
        "fr": "Modèle d'appareil",
        "es": "Modelo del dispositivo",
    },
    "sel_software": {
        "zh-CN": "软件",
        "en": "Software",
        "fr": "Logiciel",
        "es": "Software",
    },
    "sel_release": {
        "zh-CN": "版本",
        "en": "Release",
        "fr": "Version",
        "es": "Versión",
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
    "sel_dialog_title": {
        "zh-CN": "选择固件包",
        "en": "Select Firmware Package",
        "fr": "Sélectionner le paquet firmware",
        "es": "Seleccionar paquete de firmware",
    },
    "sel_dialog_filter": {
        "zh-CN": "固件包 (*.zip *.rar);;ZIP 文件 (*.zip);;RAR 文件 (*.rar);;所有文件 (*.*)",
        "en": "Firmware (*.zip *.rar);;ZIP Files (*.zip);;RAR Files (*.rar);;All Files (*.*)",
        "fr": "Firmware (*.zip *.rar);;Fichiers ZIP (*.zip);;Fichiers RAR (*.rar);;Tous les fichiers (*.*)",
        "es": "Firmware (*.zip *.rar);;Archivos ZIP (*.zip);;Archivos RAR (*.rar);;Todos los archivos (*.*)",
    },
    "sel_only_local": {
        "zh-CN": "选择本地的 .zip 或 .rar 刷机包即可离线刷机——无需联网，支持所有 Innioasis 和 Timmkoo 音频播放器。",
        "en": "Pick a local .zip or .rar firmware package for offline installation — no internet needed. Works with every Innioasis and Timmkoo audio player.",
        "fr": "Choisissez un paquet firmware local .zip ou .rar pour une installation hors ligne — aucune connexion requise. Compatible avec tous les lecteurs audio Innioasis et Timmkoo.",
        "es": "Elige un paquete de firmware local .zip o .rar para instalar sin conexión: no hace falta Internet. Funciona con todos los reproductores de audio Innioasis y Timmkoo.",
    },
    "sel_offline_install": {
        "zh-CN": "离线固件安装：可刷写所有 Innioasis 与 Timmkoo 音频播放器。任何包含 MediaTek scatter 文件的刷机包（.zip / .rar）都能在 Windows、macOS 或 Linux 上刷写。",
        "en": "Offline firmware installation for all Innioasis and Timmkoo audio players. Any firmware package (.zip / .rar) that ships a MediaTek scatter file flashes on Windows, macOS, or Linux — no internet connection required.",
        "fr": "Installation hors ligne du firmware pour tous les lecteurs audio Innioasis et Timmkoo. Tout paquet firmware (.zip / .rar) contenant un fichier scatter MediaTek se flashe sous Windows, macOS ou Linux — sans connexion Internet.",
        "es": "Instalación de firmware sin conexión para todos los reproductores de audio Innioasis y Timmkoo. Cualquier paquete de firmware (.zip / .rar) con un archivo scatter de MediaTek se puede flashear en Windows, macOS o Linux, sin conexión a Internet.",
    },
    "sel_btn_start": {
        "zh-CN": "开始刷机",
        "en": "Start Flash",
        "fr": "Démarrer le flashing",
        "es": "Iniciar flashing",
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
    "flash_connect_prompt": {
        "zh-CN": "请连接您的 {model}：先关闭设备电源，再通过 USB 线连接电脑。识别到设备后将自动开始刷机。",
        "en": "Please connect your {model}: power off the device, then connect it via USB. Flashing starts automatically when the device is detected.",
        "fr": "Veuillez connecter votre {model} : éteignez l'appareil, puis connectez-le en USB. Le flashing démarre automatiquement à la détection de l'appareil.",
        "es": "Conecta tu {model}: apaga el dispositivo y conéctalo por USB. El flashing comenzará automáticamente cuando se detecte el dispositivo.",
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
        "zh-CN": "先关闭机器电源",
        "en": "1. Power off the device",
        "fr": "1. Éteignez l'appareil",
        "es": "1. Apaga el dispositivo",
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
        "zh-CN": "返回重新选择",
        "en": "Back to Package Select",
        "fr": "Retour au choix du paquet",
        "es": "Volver a seleccionar paquete",
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
    "flash_method_note_mtk": {
        "zh-CN": "MTKClient——开源联发科刷机工具。所有平台可用，macOS 上为唯一选项。Windows 上需安装联发科 USB 驱动。",
        "en": "MTKClient — the open-source MediaTek flasher. Available on all platforms; the only option on macOS. Requires the MediaTek USB driver on Windows.",
        "fr": "MTKClient — le flasheur MediaTek open source. Disponible sur toutes les plateformes ; seul choix sous macOS. Nécessite le pilote USB MediaTek sous Windows.",
        "es": "MTKClient, el flasheador MediaTek de código abierto. Disponible en todas las plataformas; la única opción en macOS. Requiere el controlador USB de MediaTek en Windows.",
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
    "flash_complete": {
        "zh-CN": "刷机完成",
        "en": "Flash Complete",
        "fr": "Flashing terminé",
        "es": "Flashing completado",
    },
    "flash_failed": {
        "zh-CN": "刷机失败",
        "en": "Flash Failed",
        "fr": "Échec du flashing",
        "es": "Error de flashing",
    },
    "close": {
        "zh-CN": "关闭",
        "en": "Close",
        "fr": "Fermer",
        "es": "Cerrar",
    },
    "ok": {
        "zh-CN": "知道了",
        "en": "OK",
        "fr": "OK",
        "es": "Aceptar",
    },

    # Donation dialog
    "donate_title": {
        "zh-CN": "支持因尼奥斯升级工具",
        "en": "Support Innioasis Updater",
        "fr": "Soutenir Innioasis Updater",
        "es": "Apoya a Innioasis Updater",
    },
    "donate_goal_fmt": {
        "zh-CN": "<b>您已帮助我们支付本月 ${raised} 中的 ${target:.0f} 美元费用。感谢所有捐赠。</b>",
        "en": "<b>You've helped us cover ${raised} of our ${target:.0f} costs for this month. All donations are appreciated.</b>",
        "fr": "<b>Vous nous avez aidés à couvrir ${raised} de nos ${target:.0f} de frais pour ce mois-ci. Tous les dons sont appréciés.</b>",
        "es": "<b>Nos has ayudado a cubrir ${raised} de nuestros ${target:.0f} de costes de este mes. Agradecemos todas las donaciones.</b>",
    },
    "donate_headline": {
        "zh-CN": "它需要 <span style='color:#ff5252;'>你</span>。",
        "en": "It takes <span style='color:#ff5252;'>you</span>.",
        "fr": "Il faut <span style='color:#ff5252;'>vous</span>.",
        "es": "Te necesita a <span style='color:#ff5252;'>ti</span>.",
    },
    "donate_subtitle": {
        "zh-CN": "我们的项目依赖社区捐赠",
        "en": "Our projects rely on donations from the community",
        "fr": "Nos projets dépendent des dons de la communauté",
        "es": "Nuestros proyectos dependen de las donaciones de la comunidad",
    },
    "donate_this_firmware": {
        "zh-CN": "此固件",
        "en": "this firmware",
        "fr": "ce firmware",
        "es": "este firmware",
    },
    "donate_intro_success": {
        "zh-CN": "我们已将 <b>{software}</b> 安装到您的 <b>{model}</b>。<br><br>你好！我是 Ryan，Innioasis Updater、社区固件档案和主题画廊的开发者。我自掏腰包承担每月约 200 美元的服务器和存储费用，以保持一切免费和开放。如果这个工具帮助了您，请考虑捐赠——这能让这些资源持续为大家服务。",
        "en": "We've installed <b>{software}</b> on your <b>{model}</b>.<br><br>Hey! I'm Ryan, the developer behind Innioasis Updater, the Community Firmware Archive, and the Themes Gallery. I cover our ~$200 monthly server and storage costs out of pocket to keep everything free and open. If this tool helped you, please consider donating — it keeps these resources alive for everyone.",
        "fr": "Nous avons installé <b>{software}</b> sur votre <b>{model}</b>.<br><br>Salut ! Je suis Ryan, le développeur derrière Innioasis Updater, l'Archive Communautaire de Firmwares et la Galerie de Thèmes. Je couvre nos ~200 $ mensuels de serveur et de stockage de ma poche pour que tout reste gratuit et ouvert. Si cet outil vous a aidé, pensez à faire un don — cela maintient ces ressources en vie pour tout le monde.",
        "es": "Hemos instalado <b>{software}</b> en tu <b>{model}</b>.<br><br>¡Hola! Soy Ryan, el desarrollador detrás de Innioasis Updater, el Archivo Comunitario de Firmwares y la Galería de Temas. Cubro nuestros ~200 $ mensuales de servidor y almacenamiento de mi propio bolsillo para mantener todo gratis y abierto. Si esta herramienta te ha ayudado, considera hacer una donación: mantiene estos recursos vivos para todos.",
    },
    "donate_intro_general": {
        "zh-CN": "你好！我是 Ryan，Innioasis Updater、社区固件档案和主题画廊的开发者。我自掏腰包支付服务器托管、固件档案存储和域名续费，以保持一切免费。每月维护费用约 200 美元——您的任何支持都能帮助这些工具继续为下一位用户服务。",
        "en": "Hey! I'm Ryan, the developer behind Innioasis Updater, the Community Firmware Archive, and the Themes Gallery. I pay for server hosting, firmware archive storage, and domain renewals out of my own pocket to keep everything free. Monthly upkeep comes to around $200 — any support you give helps keep these tools alive for the next owner.",
        "fr": "Salut ! Je suis Ryan, le développeur derrière Innioasis Updater, l'Archive Communautaire de Firmwares et la Galerie de Thèmes. Je paie l'hébergement du serveur, le stockage des firmwares et les renouvellements de domaine de ma poche pour que tout reste gratuit. L'entretien mensuel s'élève à environ 200 $ — toute aide que vous apportez contribue à garder ces outils en vie pour le prochain utilisateur.",
        "es": "¡Hola! Soy Ryan, el desarrollador detrás de Innioasis Updater, el Archivo Comunitario de Firmwares y la Galería de Temas. Pago el alojamiento del servidor, el almacenamiento del archivo de firmwares y las renovaciones de dominio de mi bolsillo para mantener todo gratis. El mantenimiento mensual ronda los 200 $; cualquier apoyo que des ayuda a mantener estas herramientas vivas para el próximo usuario.",
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
    "donate_ticker_fmt": {
        "zh-CN": "<b>{anchor}</b> 通过 {method} 捐赠了 ${amount}",
        "en": "<b>{anchor}</b> donated ${amount} by {method}",
        "fr": "<b>{anchor}</b> a donné ${amount} via {method}",
        "es": "<b>{anchor}</b> donó ${amount} mediante {method}",
    },
    "donate_kofi": {
        "zh-CN": "Ko-fi",
        "en": "Ko-fi",
        "fr": "Ko-fi",
        "es": "Ko-fi",
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
        "zh-CN": "免费支持：加入 Honeygain",
        "en": "Contribute for free by joining Honeygain",
        "fr": "Contribuez gratuitement en rejoignant Honeygain",
        "es": "Contribuye gratis uniéndote a Honeygain",
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
    "donate_thanks": {
        "zh-CN": "感谢所有支持者与贡献者！",
        "en": "Thanks to all our supporters & contributors!",
        "fr": "Merci à tous nos soutiens et contributeurs !",
        "es": "¡Gracias a todos nuestros seguidores y colaboradores!",
    },
    "donate_copied": {
        "zh-CN": "{label} 地址已复制",
        "en": "{label} address copied",
        "fr": "Adresse {label} copiée",
        "es": "Dirección de {label} copiada",
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
}


class _Translator:
    def __init__(self):
        self._lang = "en"

    @property
    def lang(self) -> str:
        return self._lang

    def set_language(self, lang: str):
        if lang in _SUPPORTED:
            self._lang = lang

    def t(self, key: str) -> str:
        table = _STRINGS.get(key)
        if not table:
            return key
        return table.get(self._lang) or table.get("en") or key


_global_translator = _Translator()


def translator() -> _Translator:
    """Module-level translator (the app installs one global instance)."""
    return _global_translator


def tr(key: str) -> str:
    return _global_translator.t(key)
