package br.com.bannaman.markwear

import android.bluetooth.*
import android.bluetooth.le.*
import android.content.Context
import android.os.ParcelUuid
import br.com.bannaman.markwear.protocol.CompanionPolicy
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Short lab probe: public version only, no command or administration characteristic. */
class WearBleProbe(context:Context):AutoCloseable {
 private val manager=context.getSystemService(BluetoothManager::class.java)
 private var server:BluetoothGattServer?=null
 private var callback:AdvertiseCallback?=null
 @Suppress("MissingPermission") fun run():String {
  val adapter=manager.adapter?:return "BLE_ADAPTER_UNAVAILABLE"
  if(!adapter.isEnabled)return "BLE_DISABLED"
  if(!adapter.isMultipleAdvertisementSupported)return "BLE_ADVERTISING_NOT_SUPPORTED"
  val advertiser=adapter.bluetoothLeAdvertiser?:return "BLE_ADVERTISER_UNAVAILABLE"
  val opened=manager.openGattServer(appContext,object:BluetoothGattServerCallback(){
   override fun onCharacteristicReadRequest(device:BluetoothDevice,requestId:Int,offset:Int,c:BluetoothGattCharacteristic){
    val value="MARK-PROBE-1".toByteArray();if(offset !in 0..value.size){server?.sendResponse(device,requestId,BluetoothGatt.GATT_INVALID_OFFSET,offset,null);return}
    server?.sendResponse(device,requestId,BluetoothGatt.GATT_SUCCESS,offset,value.copyOfRange(offset,value.size))
   }
  })?:return "BLE_GATT_SERVER_UNAVAILABLE"
  server=opened
  val characteristic=BluetoothGattCharacteristic(java.util.UUID.fromString("2c09f06b-c34c-4c41-a736-11c421d134a2"),BluetoothGattCharacteristic.PROPERTY_READ,BluetoothGattCharacteristic.PERMISSION_READ)
  val service=BluetoothGattService(CompanionPolicy.SERVICE,BluetoothGattService.SERVICE_TYPE_PRIMARY);service.addCharacteristic(characteristic)
  if(!opened.addService(service)){close();return "BLE_SERVICE_REJECTED"}
  val latch=CountDownLatch(1);var result="BLE_ADVERTISEMENT_TIMEOUT"
  callback=object:AdvertiseCallback(){override fun onStartSuccess(settings:AdvertiseSettings){result="BLE_ADVERTISEMENT_STARTED";latch.countDown()};override fun onStartFailure(code:Int){result="BLE_ADVERTISEMENT_FAILED_$code";latch.countDown()}}
  return try {
   advertiser.startAdvertising(AdvertiseSettings.Builder().setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_POWER).setConnectable(true).setTimeout(10000).build(),AdvertiseData.Builder().addServiceUuid(ParcelUuid(CompanionPolicy.SERVICE)).setIncludeDeviceName(false).build(),callback)
   latch.await(5,TimeUnit.SECONDS);result
  }finally{close()}
 }
 private val appContext=context.applicationContext
 @Suppress("MissingPermission") override fun close(){callback?.let {runCatching {manager.adapter.bluetoothLeAdvertiser?.stopAdvertising(it)}};callback=null;runCatching {server?.close()};server=null}
}
