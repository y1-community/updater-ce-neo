#ifndef ICOMMAND_H
#define ICOMMAND_H

#include "../Public/Host.h"
#include "../BootRom/flashtoolex_api.h"
#include "../BootRom/type_define.h"
#include "../BootRom/mtk_status.h"

#include <QSharedPointer>
#include <iostream>
#include <algorithm>

namespace APCore
{

class Connection;
class ICommand
{
public:
    ICommand(APKey key):key_(key){}
    virtual ~ICommand(){}

    virtual void set_da_file(const std::string da){
        da_ = da;
    }

    virtual status_t connect(HSESSION *hs, const file_info* p_preloader_info){
        status_t status = STATUS_OK;
        uint32 cnt = 0;
        char buffer[32] = {0};
        std::string tempstr;

        const char* filter[3] =
        {
            (char*)"USB\\VID_0E8D&PID_0003",
            (char*)"USB\\VID_0E8D&PID_2000",
            (char*)"USB\\VID_0E8D&PID_2001"
        };

        char port_name[256] = {0};
        std::cout << "Wait for device. Plug in USB." <<std::endl;
        status = flashtool_waitfor_com(filter, 3, port_name);
        if(status != STATUS_OK)
        {
            goto exit;
        }

        *hs = flashtool_create_session();
        if(*hs == INVALID_HSESSION_VALUE)
        {
            goto exit;
        }

        std::cout << "Connect port: " <<port_name <<std::endl;
        status = flashtool_connect_brom(*hs, port_name, NULL, NULL, NULL);
        if(status != STATUS_OK)
        {
            goto exit;
        }

        connect_params_struct_t conn_da_params;
        conn_da_params.checksum_level = 0;
        conn_da_params.battery_setting = 2;
		conn_da_params.connect_da_end_stage = CONN_SECOND_DA;
        file_info da_info;
        memset(&da_info, 0, sizeof(da_info));
        da_info.input_type = OP_SOURCE_FILE;
        if (strlen(da_.c_str()) >= MAX_FILE_NAME_LEN)
        {
            status = STATUS_DA_FILE_INVALID;
            goto exit;
        }
        strcpy(da_info.file_path, da_.c_str());

        status = flashtool_connect_da(*hs, &da_info, p_preloader_info, NULL, &conn_da_params, NULL);
        if(status != STATUS_OK)
        {
            goto exit;
        }

        status = flashtool_device_control(*hs, DEV_DA_GET_USB_SPEED, 0, 0, buffer, 32, &cnt);
        if(status != STATUS_OK)
        {
            goto exit;
        }

        tempstr = buffer;
        std::transform(tempstr.begin(), tempstr.end(), tempstr.begin(), toupper);
        if(strcmp(tempstr.data(), "USB_FULL_SPEED") == 0)
        {
            status = flashtool_switch_com(*hs, filter+2, 1); //switch to DA com port

            if(status != STATUS_OK)
            {
                goto exit;
            }
        }

exit:
        return status;
    }

    virtual void exec(const QSharedPointer<APCore::Connection> &conn) = 0;

protected:
    APKey key_;
    std::string da_;
};

}

#endif // ICOMMAND_H
