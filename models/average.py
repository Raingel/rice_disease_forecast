# %%
import pandas as pd
import os
from datetime import datetime

# Define folder path
DATA_FOLDER = "/home/raingel/rice_blast_model_update/rice_blast_prediction/data"
OUTPUT_FILE = "/home/raingel/rice_blast_model_update/rice_blast_prediction/average_risk/daily_average_risk_per_station.csv"

# Initialize a dictionary to collect data by station and date
station_data = {}

# Scan the folder for all forecast files
for file_name in os.listdir(DATA_FOLDER):
    if file_name.endswith(".csv"):
        # Extract model type and date from file name
        parts = file_name.split("_")
        date_str = parts[0]
        model_name = parts[1].replace(".csv", "")

        try:
            # Parse date
            file_date = datetime.strptime(date_str, "%Y%m%d")

            # Load the data
            file_path = os.path.join(DATA_FOLDER, file_name)
            df = pd.read_csv(file_path)

            # Group data by station and date
            for _, row in df.iterrows():
                station_id = row["站號"]
                station_name = row["站名"]
                lat = row["lat"]
                lon = row["lon"]
                risk_value = row[model_name]

                if station_id not in station_data:
                    station_data[station_id] = {
                        "station_name": station_name,
                        "lat": lat,
                        "lon": lon,
                        "daily_risks": {}
                    }

                if date_str not in station_data[station_id]["daily_risks"]:
                    station_data[station_id]["daily_risks"][date_str] = {}

                station_data[station_id]["daily_risks"][date_str][model_name] = risk_value

        except Exception as e:
            print(f"Error processing file {file_name}: {e}")

# Calculate daily average risk values for each station and model
result = []
for station_id, data in station_data.items():
    for date_str, models in data["daily_risks"].items():
        row = {
            "station_id": station_id,
            "station_name": data["station_name"],
            "lat": data["lat"],
            "lon": data["lon"],
            "date": date_str
        }
        row.update(models)
        result.append(row)

# Convert results to a DataFrame
result_df = pd.DataFrame(result)

# Save the results to a CSV file
os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
result_df.to_csv(OUTPUT_FILE, index=False)
print(f"Daily average risk values have been saved to {OUTPUT_FILE}")

# %%
