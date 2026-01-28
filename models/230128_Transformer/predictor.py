# %%
# %%
import pandas as pd
#Suppress SettingWithCopyWarning
pd.options.mode.chained_assignment = None
import numpy as np
from tensorflow.keras.models import load_model
from tensorflow.keras import backend as K
import tensorflow_addons as tfa
from requests import get
from matplotlib import pyplot as plt
from datetime import datetime, timedelta
import os
ranger2 = tfa.optimizers.Lookahead(tfa.optimizers.RectifiedAdam(learning_rate=0.001))
ROOT = "./"

# %%
"""
def open_meteo_archive (latitude = "24.145736", longitude = "120.684075", start_date="2023-01-01", end_date="2023-01-25"):
    #URI =f"https://archive-api.open-meteo.com/v1/archive?latitude={latitude}&longitude={longitude}&start_date={start_date}&end_date={end_date}&hourly=temperature_2m,relativehumidity_2m,dewpoint_2m,apparent_temperature,precipitation,weathercode,cloudcover,direct_radiation&models=best_match&timezone=Asia%2FSingapore"
    URI=f"https://archive-api.open-meteo.com/v1/archive?latitude={latitude}&longitude={longitude}&start_date={start_date}&end_date={end_date}&hourly=temperature_2m,relativehumidity_2m,dewpoint_2m,precipitation,cloudcover,direct_radiation,windspeed_10m,winddirection_10m&models=best_match&timezone=Asia%2FSingapore"
    response = get(URI).json()
    try:
        om_df = pd.DataFrame(response['hourly'])
    except Exception as e:
        print(e, response)
    #Calculate wind vector
    radian = om_df ['winddirection_10m'] * (np.pi/180)
    om_df ['Wu'] = - om_df ['windspeed_10m'] * np.sin(radian)
    om_df ['Wv'] = - om_df ['windspeed_10m'] * np.cos(radian)
    om_df['time'] = pd.to_datetime(om_df['time'])
    om_df.set_index('time', inplace=True)
    om_df.dropna(inplace=True)
    return om_df
def open_meteo_forecast(latitude = "24.145736", longitude = "120.684075", past_days = 14):
    URI=f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&hourly=temperature_2m,relativehumidity_2m,dewpoint_2m,precipitation,cloudcover,direct_radiation,windspeed_10m,winddirection_10m&current_weather=true&past_days={past_days}&timezone=Asia%2FSingapore"
    response = get(URI).json()
    om_df = pd.DataFrame(response['hourly'])
    #Calculate wind vector
    radian = om_df ['winddirection_10m'] * (np.pi/180)
    om_df ['Wu'] = - om_df ['windspeed_10m'] * np.sin(radian)
    om_df ['Wv'] = - om_df ['windspeed_10m'] * np.cos(radian)
    om_df['time'] = pd.to_datetime(om_df['time'])
    om_df.set_index('time', inplace=True)
    return om_df
def archive_and_forecast(latitude = "24.145736", longitude = "120.684075", start_date="2023-01-01"):
    archive = open_meteo_archive(latitude, longitude, start_date, end_date = (datetime.today() - timedelta(days=7)).strftime("%Y-%m-%d"))
    forecast = open_meteo_forecast(latitude, longitude, past_days = 14)
    total = pd.concat([archive, forecast])
    #Merge archive and forecast, remove duplicates
    total[~total.index.duplicated(keep='first')]
    return total
"""
def extract_window(df, window_size, step):
    pos = 0
    while pos + window_size < len(df):
        yield df[pos:pos+window_size]
        pos += step

# %%
"""
stations = pd.read_csv("https://raw.githubusercontent.com/Raingel/weather_station_list/main/data/weather_sta_list.csv")
stations = stations[stations["撤站日期"].isnull()]
stations = stations[stations["海拔高度(m)"]<500]
"""
# %%
DAYS_AHEAD = 21
START_DATE = (datetime.today()-timedelta(days=DAYS_AHEAD)).strftime("%Y-%m-%d")
X = []
info = []
weaDf_slice = pd.DataFrame()
for f in os.scandir("../ERA5_data/"):
    if f.name.endswith(".csv"):
        print(f.name)
        try:
            sta_id, sta_name, lat, lon= f.name.replace(".csv", "").split("_")
            weaDf = pd.read_csv(f.path)
        except Exception as e:
            print(e)
            continue
        weaDf['time'] = pd.to_datetime(weaDf['time'])
        weaDf.set_index('time', inplace=True)
        weaDfmax = weaDf.resample('D').max()
        weaDfmean = weaDf.resample('D').mean()
        weaDf_slice['temperature_2m_max'] =  weaDfmax['temperature_2m'] 
        weaDf_slice['precipitation_mean'] =  weaDfmean['precipitation']
        for e in extract_window(weaDf_slice, 20, 1):
            X.append(e.to_numpy())
            info.append({"date":e.iloc[-1].name + pd.Timedelta(days=3), "sta_id":sta_id, "sta_name": sta_name ,"lat":lat, "lon":lon})

# %% [markdown]
# 

# %%
#Normalization
X=np.array(X)
X[:,:,0] = (X[:,:,0] - 10.9) / 24.3
X[:,:,1] = (X[:,:,1] - 0.0) / 6.275

# %%
model = load_model(ROOT+"/230207_Transformer_colab.h5")
pre = model.predict(np.array(X))[:,0]

# %%
#Round to 4 decimal places
pre = np.round(pre, 4)
predict_result = pd.DataFrame({ "站號": [i["sta_id"] for i in info], "站名": [i["sta_name"] for i in info],"日期": [i["date"] for i in info], "lat": [i["lat"] for i in info], "lon": [i["lon"] for i in info], "BlastTF":pre})

# %%
#Save separately by 日期
for date in predict_result["日期"].unique():
    #date To string
    ds = datetime.fromtimestamp(date.item()/10**9).strftime("%Y%m%d")
    predict_result[predict_result["日期"]==date].to_csv("../../daily_forecast_tmp/"+ds+"_BlastTF.csv", index=False, encoding="utf-8-sig")


