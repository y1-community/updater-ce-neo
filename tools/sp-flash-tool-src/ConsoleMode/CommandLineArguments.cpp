#include "CommandLineArguments.h"

#ifndef _WIN32
#include <getopt.h>
#endif

#include <iostream>
#include <stdlib.h>
#include <QString>
#include <QStringList>
#include <algorithm>
#include <cctype>
#include "../Utility/FileUtils.h"
#include "../Utility/Utils.h"
#include "../Logger/Log.h"
#include "SchemaValidator.h"
#include "../XMLParser/XMLDocument.h"
#include "../XMLParser/XMLNode.h"

using std::string;

namespace ConsoleMode
{

const string CONSOLE_MODE_USAGE(
        "\nUsage: flash_tool OPTION...\n"
        "Run FlashTool in console mode.\n"
        "\nArgument instructions:\n"
        "  -i    console mode configuration file, mandatory with config.xml.\n"
        "          flash_tool -i config.xml\n"
        "  -s    scatter file path, mandatory without config.xml.\n"
        "          flash_tool -c download -s MT6575_Anroid_scatter.txt\n"
        "  -c    flash tool features, mandatory without config.xml, just support:\n"
        "            format\n"
        "            download\n"
        "            format-download\n"
        "            firmware-upgrade\n"
		"            cert-download\n"
        "            efuse\n"
        "            dram-repair\n"
        "          flash_tool -c download -s MT6575_Anroid_scatter.txt\n"
        "  -d    download agent file path.\n"
        "          flash_tool -d MTK_AllInOne_DA.bin -s MT6575_Anroid_scatter.txt -c download\n"
		"  -e    certification file path.\n"
        "          flash_tool -e cert_file.cert -s MT6575_Anroid_scatter.txt -c cert-download\n"
        "  -p    com port, format is as COM4+COM6 (BROM download), COM5 (preloader download) on Windows "
        "and format is as 5-2+1-6(BROM download), 5-1(preloader download) on Linux.\n"
        "          flash_tool -p COM4+COM6 -i config.xml\n"
        "          flash_tool -p COM4+COM6 -s MT6575_Anroid_scatter.txt -c download\n"
        "  -r    redirect debug log to parent process.\n"
        "          flash_tool -r -i config.xml\n"
        "  -t    Specify battery mode. with, without and auto are available. if no specify the -t argument, the auto mode used."
        "        Only valid without config.xml.\n"
        "          flash_tool -d MTK_AllInOne_DA.bin  -s MT6575_Anroid_scatter.txt  -c download -t without\n"
        "  -rsc  Specify the project index in rsc.xml, only support for download/format-download/firmware-upgrade on Windows.\n"
        "          flash_tool -s MT6575_Anroid_scatter.txt -c download -rsc 0\n"
        "  -b    reboot the device.\n"
        "          flash_tool -b -i config.xml\n"
        "  -h    display help infomation and exit.\n"
        "          flash_tool -h"
        );

const QString SUPPORTED_CMDS("combo-format,download-only,format-download,firmware-upgrade,cert-download,efuse,dram-repair");

CommandLineArguments::CommandLineArguments()
    :m_szInputFilename(),
     m_szDownloadAgentFilename(),
     m_szScatterFilename(),
     m_szAuthFilename(),
     m_szCertFilename(),
     m_szCommand(),
     m_szPort(),
     m_fgRedirect(false),
     m_fgDisplayHepInfo(false),
     m_onlyOutput(false),
     m_reboot(false),
     m_battery_option("auto")
{
}

const string &CommandLineArguments::GetInputFilename() const
{
    return m_szInputFilename;
}

const string &CommandLineArguments::GetScatterFilename() const
{
    return m_szScatterFilename;
}

const string &CommandLineArguments::GetDownloadAgentFilename() const
{
    return m_szDownloadAgentFilename;
}

const string &CommandLineArguments::GetCommand() const
{
    return m_szCommand;
}

const string &CommandLineArguments::GetAuthFilename() const
{
    return m_szAuthFilename;
}

const string &CommandLineArguments::GetCertFilename() const
{
    return m_szCertFilename;
}

const std::string &CommandLineArguments::GetComPort() const
{
    return m_szPort;
}

const std::string &CommandLineArguments::GetHelpInfo() const
{
    return CONSOLE_MODE_USAGE;
}

std::string CommandLineArguments::GetRSCIndexStr() const
{
    return m_rsc_index_str;
}

bool CommandLineArguments::DoRedirect() const
{
    return m_fgRedirect;
}

bool CommandLineArguments::DisplayHelpInfo() const
{
    return m_fgDisplayHepInfo;
}

bool CommandLineArguments::Parse(int argc, char **argv)
{
    bool result = false;
#ifndef _WIN32
    //int opt;

    while (1)
    {
        int c = getopt(argc, argv, "i:d:c:s:p:rhbt:e:");

        if (c == -1)
        {
            break;
        }

        switch (c)
        {
            case 'i':
                m_szInputFilename = optarg;
                result = true;
                break;

            case 'r':
                m_fgRedirect = true;
                break;

            case 'd':
                m_szDownloadAgentFilename = optarg;
                break;

            case 's':
                m_szScatterFilename = optarg;
                if(!m_szCommand.empty())
                {
                    if (m_szCommand == "cert-download")
                    {
                        if (!m_szCertFilename.empty())
                        {
                            result = true;
                        }
                    }
                    else
                    {
                        result = true;
                    }
                }
                break;
            case 'c':
                m_szCommand = optarg;
                if(!m_szScatterFilename.empty())
                {
                    if (m_szCommand == "cert-download")
                    {
                        if (!m_szCertFilename.empty())
                        {
                            result = true;
                        }
                    }
                    else
                    {
                        result = true;
                    }
                }
                break;

            case 'p':
                m_szPort = optarg;
                break;

            case 'h':
                m_fgDisplayHepInfo = true;
                result = true;
                break;

            case 'b':
                m_reboot = true;
                break;

            case 't':
                if((stricmp(optarg, "with") != 0)
                        && (stricmp(optarg, "without") != 0)
                        && (stricmp(optarg, "auto") != 0))
                {
                    return false;
                }

                m_battery_option = optarg;
                break;
            case 'e':
                m_szCertFilename = optarg;
                if (!m_szScatterFilename.empty() && !m_szCommand.empty())
                {
                    result = true;
                }
                break;

            default:
               return false;
        }
    }

    return  result;

#else
    for(int i = 1; i < argc; i++)
    {
        if(argv[i][0] == '-')
        {
            switch(tolower(argv[i][1]))
            {
            case 'i':
                if (++i >= argc)
                {
                    return false;
                }
                m_szInputFilename = argv[i];
                if (!m_rsc_index_str.empty())
                {
                    return false;
                }
                result = true;
                break;

            case 'r':
            {
                std::string arg = argv[i];
                std::transform(arg.begin(), arg.end(), arg.begin(), tolower);
                if (arg == "-r")
                {
                    m_fgRedirect = true;
                }
                else if (arg == "-rsc")
                {
                    if (++i >= argc) return false;
                    if (!m_szInputFilename.empty()) return false;
                    m_rsc_index_str = argv[i];
                }
                else
                {
                    assert(false);
                }
                break;
            }
            case 'd':
                if (++i >= argc)
                {
                    return false;
                }
                m_szDownloadAgentFilename = argv[i];
                break;

            case 's':
                if (++i >= argc)
                {
                    return false;
                }
                m_szScatterFilename = argv[i];
                if(!m_szCommand.empty())
                {
                    if (m_szCommand == "cert-download")
                    {
                        if (!m_szCertFilename.empty())
                        {
                            result = true;
                        }
                    }
                    else
                    {
                        result = true;
                    }
                }
                break;
            case 'c':
                if (++i >= argc)
                {
                    return false;
                }
                m_szCommand = argv[i];
                if(!m_szScatterFilename.empty())
                {
                    if (m_szCommand == "cert-download")
                    {
                        if (!m_szCertFilename.empty())
                        {
                            result = true;
                        }
                    }
                    else
                    {
                        result = true;
                    }
                }
                break;
            case 'p':
                if (++i >= argc)
                {
                    return false;
                }
                m_szPort = argv[i];
                break;

            case 'h':
                m_fgDisplayHepInfo = true;
                result = true;
                break;

            case 'o':
                m_onlyOutput = true;
                break;

            case 'b':
                m_reboot = true;
                break;

            case 't':
                if (++i >= argc)
                {
                    return false;
                }
                if((stricmp(argv[i], "with") != 0)
                        && (stricmp(argv[i], "without") != 0)
                        && (stricmp(argv[i], "auto") != 0))
                {
                    return false;
                }

                m_battery_option = argv[i];
                break;
            case 'e':
                if (++i >= argc)
                {
                    return false;
                }
                m_szCertFilename = argv[i];
                if (!m_szScatterFilename.empty() && !m_szCommand.empty())
                {
                    result = true;
                }
                break;

            default:
               return false;
            }
        }
    }

    return result;

#endif
}

bool hasNotDigit(const char ch)
{
    return !isdigit(ch);
}

bool CommandLineArguments::Validate()
{
    if (!ValidateInputFile())
    {
        return false;
    }

    if(!this->m_szScatterFilename.empty() && !FileUtils::IsFileExist(m_szScatterFilename))
    {
        LOGE("\"%s\": file not found", m_szScatterFilename.c_str());
        return false;
    }

    if(!this->m_szDownloadAgentFilename.empty() && !FileUtils::IsFileExist(m_szDownloadAgentFilename))
    {
        LOGE("\"%s\": file not found", m_szDownloadAgentFilename.c_str());
        return false;
    }

    if(!this->m_szCommand.empty())
    {
        transform(m_szCommand.begin(),m_szCommand.end(),m_szCommand.begin(),tolower);
        if("download" ==m_szCommand)
        {
            m_szCommand = "download-only";
        }
        else if ("format" == m_szCommand)
        {
            m_szCommand = "combo-format";
        }
        QString cmd = QString::fromStdString(m_szCommand);

        QStringList list = SUPPORTED_CMDS.split(",");
        if(!list.contains(cmd))
        {
            LOGE("Command \"%s\":is not support!", m_szCommand.c_str());
            return false;
        }
    }

    if(!this->m_szPort.empty())
    {
        std::string::size_type index = m_szPort.find("+");
        if(index != std::string::npos)
        {
            std::string full_com_port = m_szPort.substr(0, index);
            std::string high_com_port = m_szPort.substr(index + 1);
            if (!valid_com_port(full_com_port.c_str()) || !valid_com_port(high_com_port.c_str()))
            {
                return false;
            }
        }
        else
        {
            if (!valid_com_port(m_szPort.c_str()))
            {
                return false;
            }
        }
    }

    if(!this->m_szAuthFilename.empty() && !FileUtils::IsFileExist(m_szAuthFilename))
    {
        LOGE("\"%s\": file not found", m_szAuthFilename.c_str());
        return false;
    }

    if(!this->m_szCertFilename.empty() && !FileUtils::IsFileExist(m_szCertFilename))
    {
        LOGE("\"%s\": file not found", m_szCertFilename.c_str());
        return false;
    }

    if (!m_rsc_index_str.empty() &&
            std::find_if(m_rsc_index_str.begin(), m_rsc_index_str.end(), hasNotDigit) != m_rsc_index_str.end())
    {
        LOGE("rsc index (%s) should be integer not less than zero.", m_rsc_index_str.c_str());
        return false;
    }

    return true;
}

bool CommandLineArguments::valid_com_port(const QString &usb_com_port) const
{
#ifdef _WIN32
    int port_num = Utils::GetPortNumFromStr(usb_com_port);
    return port_num > 0;
#else
    QRegExp regex("^\\d+-\\d+(?:\\.\\d+)*$");
    int pos = regex.indexIn(usb_com_port);
    return pos != -1;
#endif
}

bool CommandLineArguments::ValidateInputFile() const
{
    // validate configuration file
    if(!m_szInputFilename.empty())
    {
        if (!FileUtils::IsFileExist(m_szInputFilename))
        {
            LOGI("\"%s\": file not found", m_szInputFilename.c_str());
            return false;
        }

        SchemaValidator validator(Utils::GetTextCodec()->toUnicode(ABS_PATH_C("console_mode.xsd")));

        try
        {
            validator.Validate(QString::fromStdString(m_szInputFilename));
        }
        catch(...)
        {
            LOGI("Invalid xml file, please check the input file and try again.");
            return false;
        }
    }
    return true;
}

}
