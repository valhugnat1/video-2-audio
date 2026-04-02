curl -X POST https://your-function-id.functions.fnc.fr-par.scw.cloud/convert \
     -H "Content-Type: application/json" \
     -d '{
           "video_url": "https://drive.google.com/file/d/17IlHTmWUGf3yOAlzO4Nnx7ANX3EjQSX4/view",
           "folder_url": "https://drive.google.com/drive/folders/17We1iX19Osse1tSX3JIg3DicwqKIUlmR"
         }'
