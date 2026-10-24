#include "ReadbackCommand.h"
#include "../Logger/Log.h"
#include "../Err/Exception.h"
#include "../Utility/FileUtils.h"
#include "../Utility/Utils.h"

namespace APCore
{

ReadbackCommand::ReadbackCommand(APKey key)
    :ICommand(key), readback_arg_(), m_is_scatter_ver2(false)
{
}

ReadbackCommand::~ReadbackCommand()
{

}

void ReadbackCommand::exec(const QSharedPointer<Connection> &conn)
{
    conn->ConnectDA();

    if (m_is_scatter_ver2)
    {
        HW_StorageType_E storage_type = getDeviceStorageType(&(conn->da_report()));
        if (storage_type == HW_STORAGE_NONE || storage_type == HW_STORAGE_TYPE_END) {
            LOGE("error: unknown storage type!");
            THROW_BROM_EXCEPTION(STATUS_UNKNOWN_STORAGE_TYPE, 0);
        }
        readback_arg_.set_storage_type(storage_type);
    }
    LOGI("executing Readback command...");

    FlashTool_Readback_Result rb_result;
    memset(&rb_result, 0, sizeof(FlashTool_Readback_Result));

    int ret = FlashTool_Readback(
                conn->FTHandle(),
                readback_arg_.raw_arg(),
                &rb_result);
    LOGD("Readback result: %s(%d)", StatusToString(ret), ret);
    if(ret != S_DONE)
    {
        THROW_BROM_EXCEPTION(ret,0);
    }

    LOGI("Readback Succeeded.");
}

}
