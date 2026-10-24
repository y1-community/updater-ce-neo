#ifndef READBACKCOMMAND_H
#define READBACKCOMMAND_H

#include "ICommand.h"
#include "../Arg/BromReadbackArg.h"
#include "../Conn/Connection.h"
#include "../Public/AppTypes.h"

namespace APCore
{

class ReadbackCommand : public ICommand
{
public:
    ReadbackCommand(APKey key);
    virtual ~ReadbackCommand();

    virtual void exec(const QSharedPointer<Connection> &conn);

    BromReadbackArg& readback_arg(){ return readback_arg_;}
    void set_is_scatter_ver2(bool is_scatter_ver2)
    {
        m_is_scatter_ver2 = is_scatter_ver2;
    }

private:
    friend class ReadbackSetting;
    BromReadbackArg readback_arg_;
    bool m_is_scatter_ver2;
};

}

#endif // READBACKCOMMAND_H
