#include "DLWithoutScatterCommand.h"
#include "../Logger/Log.h"
#include "../Err/Exception.h"
#include "../BootRom/flashtoolex_api.h"
#include "../BootRom/type_define.h"
#include "../BootRom/mtk_status.h"

namespace APCore
{
static int getPreloaderIndex(const op_part_list_t* list, int count)
{
    for(int i=0; i<count; i++)
    {
        if(strcmp(list[i].part_name, "preloader") == 0)
            return i;
    }
    return -1;
}

DLWithoutScatterCommand::DLWithoutScatterCommand(APKey key):
    ICommand(key),
    cb_op_progress_(NULL),
    cb_stage_message_(NULL)
{
}

DLWithoutScatterCommand::~DLWithoutScatterCommand()
{

}

void DLWithoutScatterCommand::get_partition_list(op_part_list_t* list, int count)
{
    if (list){
        for(int i = 0; i < count; i++){
            op_part_list_t item = part_list_.at(i);

            strncpy(list[i].part_name, item.part_name, strlen(item.part_name) + 1);
            strncpy(list[i].file_path, item.file_path, strlen(item.file_path) + 1);
        }
    }
}

void DLWithoutScatterCommand::exec(const QSharedPointer<Connection> &conn){
  //  conn->ConnectDA();
    Q_UNUSED(conn);

    HSESSION hs;

    int status = STATUS_OK;

    int count = part_list_.size();

    op_part_list_t *flist = new op_part_list_t[count];

    get_partition_list(flist, count);

    callbacks_struct_t cbs;
    memset(&cbs, 0, sizeof(cbs));

    cbs.cb_op_progress = cb_op_progress_ == NULL ? cb_dl_operation_progress : cb_op_progress_;

    cbs.cb_stage_message = cb_stage_message_ == NULL ? cb_dl_stage_message: cb_stage_message_;

    //prepare pl_info
    file_info pl_info;
    memset(&pl_info, 0, sizeof(pl_info));
    pl_info.input_type = OP_SOURCE_FILE;
    int pl_index = getPreloaderIndex(flist, count);
    LOGD("pl_index: %d", pl_index);
    if(pl_index != -1)
    {
        strcpy(pl_info.file_path, flist[pl_index].file_path);
        LOGD("pl_path: %s", pl_info.file_path);
    }

    if(connect(&hs, pl_index == -1 ? NULL : &pl_info) != STATUS_OK){
        goto exit;
    }

    LOGI("Begin executing download without scatter command...");

    status = flashtool_download(
                hs,
                NULL,
                flist,
                part_list_.size(),
                &cbs);

    LOGI("Download result: %s(%d)", StatusToString(status), status);

    if(status != STATUS_OK)
    {
        goto exit;
    }

    LOGI("Download Succeeded.");

exit:
    delete[] flist;
    flist = NULL;

    struct reboot_option option = {0};
    option.is_dev_reboot = 1;
    option.timeout_ms = 3000;

    flashtool_shutdown_device(hs, option);

    flashtool_destroy_session(hs);

    if(status != STATUS_OK)
    {
       THROW_BROM_EXCEPTION(status,0);
    }
}

void DLWithoutScatterCommand::cb_dl_operation_progress(void* arg, enum transfer_phase phase,
                                                       unsigned int progress,
                                                       unsigned long long data_xferd,
                                                       unsigned long long data_total,
                                                       const struct cbs_additional_info * cbs_add_info)
{
    Q_UNUSED(arg);
    Q_UNUSED(data_xferd);
    Q_UNUSED(data_total);
    Q_UNUSED(cbs_add_info);
   // unsigned long long  total_bytes = static_cast<unsigned long long>(static_cast<float>(data_xferd)*progress/100);
    if(phase == TPHASE_LOADER)
    {
        if(progress == 0)
        {
            LOGI("Download Boot Loader 0%");
        }
        else
        {
            LOGI("Download Boot Loader %d", progress);
        }
    }
    else
    {
        if(progress == 0)
        {
            LOGI("Download Flash 0%");
        }
        else
        {
            LOGI("Download Flash %d", progress);
        }
    }
}

void DLWithoutScatterCommand::cb_dl_stage_message(void *usr_arg, const char *message)
{
    Q_UNUSED(usr_arg);

    std::cout<< "Stage: " << message << std::endl;
}

}

