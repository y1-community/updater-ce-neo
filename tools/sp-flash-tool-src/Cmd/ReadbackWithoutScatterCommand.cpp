#include "ReadbackWithoutScatterCommand.h"
#include "../Logger/Log.h"
#include "../Err/Exception.h"
#include "../BootRom/flashtoolex_api.h"
#include "../BootRom/type_define.h"

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

ReadbackWithoutScatterCommand::ReadbackWithoutScatterCommand(APKey key):
    ICommand(key)
{
}

ReadbackWithoutScatterCommand::~ReadbackWithoutScatterCommand()
{

}

void ReadbackWithoutScatterCommand::get_partition_list(op_part_list_t* list, int count)
{
    if (list){
        for(int i = 0; i < count; i++){
            op_part_list_t item = part_list_.at(i);

            strncpy(list[i].part_name, item.part_name, strlen(item.part_name) + 1);
            strncpy(list[i].file_path, item.file_path, strlen(item.file_path) + 1);
        }
    }
}

void ReadbackWithoutScatterCommand::UpdateUploadPartitionInfo(partition_info_list_t* pReadback_part_list, const op_part_list_t* flist, partition_info_list_t* p_part_list)
{
    int count = part_list_.size();
	int readbackCount = 0;
    for(int i = 0; i < count; i++)
    {
    	for (int j = 0; j < p_part_list->count; ++j)
    	{
    		if(strcmp(p_part_list->part[j].part_name, flist[i].part_name) == 0)
	        {
	        	memcpy(&(pReadback_part_list->part[readbackCount]), &(p_part_list->part[j]), sizeof(partition_info_t));
				if (strcmp(pReadback_part_list->part[readbackCount].file_name, flist[i].file_path) != 0)
				{
					strncpy(pReadback_part_list->part[readbackCount].file_name, flist[i].file_path, strlen(flist[i].file_path) + 1);
				}
				++readbackCount;
	            break;
	        }
    	}
    }
	pReadback_part_list->count = readbackCount;
}

void ReadbackWithoutScatterCommand::exec(const QSharedPointer<Connection> &conn){
    Q_UNUSED(conn);

    HSESSION hs;

    int status = STATUS_OK;
    partition_info_list_t partInfoList;
    int count = part_list_.size();
    op_part_list_t *flist = new op_part_list_t[count];
    get_partition_list(flist, count);
    callbacks_struct_t cbs;
    memset(&cbs, 0, sizeof(cbs));

    //prepare pl_info
    file_info pl_info;
    memset(&pl_info, 0, sizeof(pl_info));
    pl_info.input_type = OP_SOURCE_FILE;
    int pl_index = getPreloaderIndex(flist, count);
    if(pl_index != -1)
    {
        strcpy(pl_info.file_path, flist[pl_index].file_path);
    }

    if(connect(&hs, pl_index == -1 ? NULL : &pl_info) != STATUS_OK){
        goto exit;
    }

    status = flashtool_read_partition_table(hs, &partInfoList);
    if(status != S_DONE)
    {
        goto exit;
    }

	partition_info_list_t readback_partInfoList;
    UpdateUploadPartitionInfo(&readback_partInfoList, flist, &partInfoList);
    cbs.cb_op_progress = cb_rb_operation_progress;
    cbs.cb_stage_message = cb_rb_stage_message;

    //conn->ConnectDA();

    LOGI("Begin executing readback without scatter command...");

    status = flashtool_upload(
                hs,
                &readback_partInfoList,
                &cbs);

    LOGI("Readback result: %s(%d)", StatusToString(status), status);

    if(status != S_DONE)
    {
        goto exit;
    }

    LOGI("Readback Succeeded.");

exit:
    delete[] flist;
    flist = NULL;
    flashtool_destroy_session(hs);

    if(status != S_DONE)
    {
        THROW_BROM_EXCEPTION(status,0);
    }
}

void ReadbackWithoutScatterCommand::cb_rb_operation_progress(void* arg, enum transfer_phase phase,
                                                             unsigned int progress,
                                                             unsigned long long data_xferd,
                                                             unsigned long long data_total,
                                                             const struct cbs_additional_info * cbs_add_info)
{
    Q_UNUSED(arg);
    Q_UNUSED(phase);
    Q_UNUSED(data_xferd);
    Q_UNUSED(data_total);
    Q_UNUSED(cbs_add_info);

    LOGI("Readback percent: %d", progress);
}

void ReadbackWithoutScatterCommand::cb_rb_stage_message(void *usr_arg, const char *message)
{
    Q_UNUSED(usr_arg);

    std::cout<< "Stage: " << message << std::endl;
}

}
