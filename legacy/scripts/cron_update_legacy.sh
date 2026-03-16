#!/bin/bash

# Debug information
echo "Script started at $(date)" >> /home/raingel/rice_blast_model_update/debug.log

# Change to the correct directory
cd /home/raingel/rice_blast_model_update/rice_blast_prediction || {
  echo "Failed to change directory to rice_blast_prediction" >> /home/raingel/rice_blast_model_update/debug.log
  exit 1
}

# Update Git repository
git fetch origin
git reset --hard origin/master 

# Activate Conda environment
source /home/raingel/anaconda3/etc/profile.d/conda.sh
conda activate keras2.10 || {
  echo "Failed to activate Conda environment"
  exit 1
}

# Check Python environment
echo "Using Python: $(which python)"



# Execute scripts
cd /home/raingel/rice_blast_model_update/models || {
  echo "Failed to change directory to model"
  exit 1
}

python ERA5_current_download_cron.py 
cd /home/raingel/rice_blast_model_update/models/BlastLSTLS || {
  echo "Failed to change directory to BlastLSTLS" 
  exit 1
}
python cron_predict.py 

cd /home/raingel/rice_blast_model_update/models/230127_GRU || {
  echo "Failed to change directory to BlastGRU" 
  exit 1
}
python predictor.py

cd /home/raingel/rice_blast_model_update/models/BLBTSLS || {
  echo "Failed to change directory to BLBTSLS" 
  exit 1
}
python predict.py

cd /home/raingel/rice_blast_model_update/models/230128_Transformer || {
  echo "Failed to change directory to 230128_Transformer" 
  exit 1
}
python predictor_250628.py

cd /home/raingel/rice_blast_model_update/models/BlastDT2 || {
  echo "Failed to change directory to BlastDT2" 
  exit 1
}
python fetch_and_convert.py

cd /home/raingel/rice_blast_model_update/models || {
  echo "Failed to change directory" 
  exit 1
}
python recent_forecast_organizer.py
python crop_season_avg.py

# Commit updates to Git
cd /home/raingel/rice_blast_model_update/rice_blast_prediction || exit
if [ -n "$(git status --porcelain)" ]; then
    git add .
    git commit -m "Update prediction data on $(date '+%Y-%m-%d %H:%M:%S')"
    git push origin master
fi



# Debug information
echo "Script completed at $(date)" 
