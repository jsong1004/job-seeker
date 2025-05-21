from flask import Flask, render_template, request, jsonify
import os
# Import necessary libraries
from serpapi import GoogleSearch
from openai import OpenAI
from supabase import create_client, Client
from dotenv import load_dotenv
import pandas as pd # Import pandas
from datetime import datetime # Import datetime

# Load environment variables from .env file only if not in a known cloud environment
# K_SERVICE is set by Cloud Run. Add other indicators if needed for other platforms.
if not os.getenv('K_SERVICE'):
    load_dotenv()
    print("Loaded environment variables from .env file for local development.")
else:
    print("Running in a cloud environment. Expecting environment variables to be set by the platform.")

app = Flask(__name__)

# Configure API keys from environment variables
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash-preview-05-20")
HTTP_REFERER = os.getenv("HTTP_REFERER", "http://localhost:5001")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# --- Excel File Configuration ---
EXCEL_FILE_PATH = 'job_applications.xlsx'

# --- Input Validation ---
# Basic check if keys are loaded (you might want more robust checks)
if not all([SERPAPI_API_KEY, OPENROUTER_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    print("Warning: One or more API keys or Supabase credentials are not set in the .env file.")
    # You could raise an error, use default values, or disable features
    # For now, we'll allow the app to run but API calls will likely fail

# Initialize Supabase client
# Ensure SUPABASE_URL and SUPABASE_KEY are loaded before uncommenting
supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
     try:
         supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
         print("Supabase client initialized.")
     except Exception as e:
         print(f"Error initializing Supabase client: {e}")
else:
     print("Supabase URL or Key not found. Skipping Supabase initialization.")

# Initialize OpenRouter client
client = None
if OPENROUTER_API_KEY:
    try:
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY,
            default_headers={
                "HTTP-Referer": HTTP_REFERER,
                "X-Title": "Job Seeker Lite"  # Your site name
            }
        )
        print(f"OpenRouter client initialized with model: {OPENROUTER_MODEL}")
    except Exception as e:
        print(f"Error initializing OpenRouter client: {e}")
else:
    print("OpenRouter API Key not found. Skipping OpenRouter configuration.")

def summarize_description(description):
    """Summarizes job description using OpenRouter API."""
    if not client:
        print("OpenRouter client not initialized. Skipping summarization.")
        return "Summarization unavailable."
    if not description:
        return "No description provided."

    prompt = f"Summarize the following job description in less than 50 words:\n\n{description}"
    try:
        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        if response.choices and len(response.choices) > 0:
            summary = response.choices[0].message.content
            return summary.strip()
        else:
            print("No response content received from OpenRouter")
            return "Could not generate summary."
    except Exception as e:
        print(f"Error calling OpenRouter API: {e}")
        return "Error during summarization."

# --- Function to Save Data to Excel ---
def save_to_excel(job_details):
    """Appends job application details to an Excel file."""
    try:
        # Prepare data row matching the specified columns
        data_to_save = {
            'Date of Contact': [datetime.now().strftime('%m/%d/%Y')], # Use current date
            'Employer': [job_details.get('company_name', 'N/A')],
            'Job Title': [job_details.get('title', 'N/A')],
            'Method of Contact': [job_details.get('via', 'N/A')], # Use the 'via' field
            'Type of Contact': ['Application'], # Default value
            'Result of Contact': ['Waiting for response'] # Default value
        }
        df_new_row = pd.DataFrame(data_to_save)

        # Check if file exists to append or create new
        if os.path.exists(EXCEL_FILE_PATH):
            # Read the existing file
            # Use engine='openpyxl' if default engine causes issues
            existing_df = pd.read_excel(EXCEL_FILE_PATH, engine='openpyxl')
            # Append the new data
            updated_df = pd.concat([existing_df, df_new_row], ignore_index=True)
            # Save the updated data back to the file
            updated_df.to_excel(EXCEL_FILE_PATH, index=False, engine='openpyxl')
            print(f"Appended job '{job_details.get('title')}' to {EXCEL_FILE_PATH}")
        else:
            # Create a new file with the data
            df_new_row.to_excel(EXCEL_FILE_PATH, index=False, engine='openpyxl')
            print(f"Created {EXCEL_FILE_PATH} and saved job '{job_details.get('title')}'")

    except Exception as e:
        print(f"Error saving job '{job_details.get('title')}' to Excel: {e}")


@app.route('/', methods=['GET', 'POST'])
def index():
    jobs_list = [] # Initialize with empty list for GET requests
    error_message = None # To display errors on the page

    if request.method == 'POST':
        job_title = request.form.get('job_title')
        location = request.form.get('location')
        print(f"Searching for: {job_title} in {location}")

        if not SERPAPI_API_KEY:
            error_message = "SerpApi API Key is not configured."
            return render_template('index.html', jobs=jobs_list, error=error_message)

        # --- SerpApi Job Search ---
        try:
            params = {
                "engine": "google_jobs",
                "q": f"{job_title} {location}",
                "location": location,
                "api_key": SERPAPI_API_KEY
            }
            search = GoogleSearch(params)
            results = search.get_dict()
            serpapi_jobs = results.get('jobs_results', [])
            print(f"Found {len(serpapi_jobs)} jobs via SerpApi.")

            processed_jobs = []
            for job in serpapi_jobs[:10]: # Limit results for now
                description = job.get('description', '')
                # --- OpenRouter Summarization ---
                summary = summarize_description(description)
                # --- Extract 'via' field ---
                via_source = job.get('via', 'Unknown') # Get 'via' or default to 'Unknown'
                # --- Extract and process 'job_highlights' ---
                highlights = job.get('job_highlights', {})
                job_highlights_str = ""
                if isinstance(highlights, dict):
                    # Assuming highlights is a dict where values are lists of strings
                    all_highlights = []
                    for key, value_list in highlights.items():
                        if isinstance(value_list, list):
                            all_highlights.extend(value_list)
                        elif isinstance(value_list, str):
                            all_highlights.append(value_list)
                    job_highlights_str = ", ".join(all_highlights)
                elif isinstance(highlights, list):
                    job_highlights_str = ", ".join(str(item) for item in highlights)
                elif isinstance(highlights, str):
                    job_highlights_str = highlights


                job_data = {
                    'company_name': job.get('company_name'),
                    'title': job.get('title'),
                    'location': job.get('location', location), # Use original location if not found
                    'description': description,
                    'summary': summary,
                    'extensions': ", ".join(job.get('detected_extensions', {}).keys()),
                    'via': via_source, # Add the 'via' field here
                    'job_highlights': job_highlights_str # Add processed job_highlights here
                }
                processed_jobs.append(job_data)

                # --- Save to Supabase ---
                if supabase:
                    try:
                        # Ensure column names here match your Supabase table exactly
                        data, count = supabase.table('jobs').insert(job_data).execute()
                        # Basic check for insertion success (you might need more specific checks)
                        if count and len(count) > 1 and count[1]:
                             print(f"Saved job '{job_data['title']}' to Supabase.")
                        else:
                             # Log the actual response data for debugging if insertion fails
                             print(f"Failed to save job '{job_data['title']}' to Supabase. Response: {data}")
                    except Exception as e:
                        print(f"Error saving job '{job_data['title']}' to Supabase: {e}")
                else:
                    print("Supabase client not initialized. Skipping database save.")

                # --- Save to Excel ---
                # save_to_excel(job_data) # Call the function to save to Excel

            jobs_list = processed_jobs # Use the processed jobs for display

        except Exception as e:
            print(f"Error during SerpApi search or processing: {e}")
            error_message = f"An error occurred during the job search: {e}"
            # Optionally, you could try fetching previously saved jobs from Supabase here as a fallback
            # if supabase:
            #     try:
            #         response = supabase.table('jobs').select("*").limit(20).execute() # Example fetch
            #         jobs_list = response.data
            #     except Exception as db_e:
            #         print(f"Error fetching from Supabase: {db_e}")


    # For GET request or after POST processing, render the page
    # If it was a POST and no error, jobs_list contains the new results
    # If it was GET or POST with error, jobs_list might be empty or fallback data
    return render_template('index.html', jobs=jobs_list, error=error_message)


if __name__ == '__main__':
    # Use port from environment variable if available, otherwise default to 5001 (changed from 5000)
    port = int(os.environ.get('PORT', 5001))
    # Set debug=False for production
    app.run(debug=True, host='0.0.0.0', port=port)