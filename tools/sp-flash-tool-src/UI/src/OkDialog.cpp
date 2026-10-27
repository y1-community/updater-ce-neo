#include "OkDialog.h"
#include "ui_OkDialog.h"
#include "../../Host/Inc/RuntimeMemory.h"
#include <QDesktopServices>
#include <QUrl>

OkDialog::OkDialog(QWidget *parent) :
    QDialog(parent),
    ui(new Ui::OkDialog)
{
    ui->setupUi(this);

    this->setWindowFlags(this->windowFlags() & ~Qt::WindowContextHelpButtonHint);
}

OkDialog::~OkDialog()
{
    if(ui)
    {
        delete ui;
        ui = NULL;
    }
}

void OkDialog::on_pushButton_buyCoffee_clicked()
{
    QDesktopServices::openUrl(QUrl("https://ko-fi.com/teamslide"));
}
