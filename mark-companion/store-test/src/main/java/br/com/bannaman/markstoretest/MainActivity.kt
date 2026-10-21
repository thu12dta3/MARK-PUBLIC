package br.com.bannaman.markstoretest
import android.app.Activity
import android.os.Bundle
import android.widget.TextView
class MainActivity:Activity(){override fun onCreate(state:Bundle?){super.onCreate(state);setContentView(TextView(this).apply {text="MARK Store\nTeste de instalação\n"+packageManager.getPackageInfo(packageName,0).versionName;gravity=17;textSize=18f;setTextColor(0xff79dde0.toInt());setBackgroundColor(0xff05080b.toInt())})}}
