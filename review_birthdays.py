"""
review_birthdays.py

Interactively reviews every Google Contact that has a birthday set.
For each one, you choose whether to keep the birthday or remove it.
Removed birthdays are not deleted outright.
Instead, they are appended to that contact's notes field as "Birthday: <date>",
and the structured birthday field is cleared so it stops showing up in Google Calendar.

Existing notes are never overwritten.
The script always reads the current note first and appends to it.

SETUP (one-time):
1. Go to https://console.cloud.google.com/ and create a project (or use an existing one).
2. Enable the "Google People API" for that project.
3. Go to "APIs & Services" -> "Credentials" -> "Create Credentials" -> "OAuth client ID".
   Choose "Desktop app" as the application type.
4. Download the resulting JSON file, rename it to credentials.json,
   and place it in the same folder as this script.
5. Install dependencies:
   pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
6. Run the script:
   python review_birthdays.py

The first run will open a browser window asking you to log in and approve access.
A token.json file will be saved afterward so you don't have to log in every time.
"""

# Standard library import used to check whether a file (token.json) already exists on disk.
import os.path

# These imports handle the OAuth2 login flow and the saved-credentials format Google uses.
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# This import gives us the actual client used to call the People API.
from googleapiclient.discovery import build

# This defines what level of access we are requesting from Google.
# "contacts" (not "contacts.readonly") is required because we need to both read and write.
SCOPES = ["https://www.googleapis.com/auth/contacts"]


def get_credentials():
    """
    Handles logging in to Google and returns valid credentials.
    Reuses a saved token.json if one exists, otherwise runs the browser login flow.
    """
    creds = None

    # If we've logged in before, token.json will hold our saved access/refresh tokens.
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    # If there are no valid credentials available, we need to either refresh or log in fresh.
    if not creds or not creds.valid:

        # If we have an expired token but also a refresh token, just refresh silently.
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Otherwise, run the full browser-based login flow using credentials.json.
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)

            # open_browser=False stops it from trying (and failing) to launch a browser itself.
            # It will just print the URL for you to open manually instead, same as before,
            # but without the wall of "not found" errors.
            creds = flow.run_local_server(port=0, open_browser=False)

        # Save the credentials for next time, so we don't have to log in again.
        with open("token.json", "w") as token_file:
            token_file.write(creds.to_json())

    return creds


def format_birthday(birthday_obj):
    """
    Converts a People API birthday object into a readable string like "1990-03-05" or "03-05".
    Google stores birthdays as either a full date or a date with no year.
    """
    date = birthday_obj.get("date", {})
    year = date.get("year")
    month = date.get("month")
    day = date.get("day")

    # If there's no day/month at all, we can't do anything useful with it.
    if not month or not day:
        return None

    # Zero-pad month and day so the output always looks like "03" instead of "3".
    month_str = str(month).zfill(2)
    day_str = str(day).zfill(2)

    # Include the year only if Google actually has one on file for this contact.
    if year:
        return f"{year}-{month_str}-{day_str}"
    else:
        return f"{month_str}-{day_str}"


def fetch_contacts_with_birthdays(service):
    """
    Pulls every contact that has at least one birthday set.
    Requests names, birthdays, and biographies (the notes field) together
    so we don't need a second API call per contact later.
    """
    contacts = []

    # This will hold the pagination cursor Google gives us for large contact lists.
    next_page_token = None

    while True:
        # Ask for one page of connections (Google's term for "your contacts").
        response = service.people().connections().list(
            resourceName="people/me",
            pageSize=200,
            personFields="names,birthdays,biographies",
            pageToken=next_page_token,
        ).execute()

        # Add this page's contacts to our running list.
        connections = response.get("connections", [])
        contacts.extend(connections)

        # Move to the next page if there is one, otherwise stop.
        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            break

    # Filter down to only contacts that actually have a birthday set.
    contacts_with_birthdays = [c for c in contacts if c.get("birthdays")]

    return contacts_with_birthdays


def get_display_name(contact):
    """
    Pulls a human-readable name out of a contact object.
    Falls back to "(no name)" if Google has no name on file, which does happen sometimes.
    """
    names = contact.get("names")

    if names:
        return names[0].get("displayName", "(no name)")
    else:
        return "(no name)"


def get_existing_note(contact):
    """
    Returns the current notes/biography text for a contact, or an empty string if none exists.
    This is what we append to, so we never lose what was already written there.
    """
    biographies = contact.get("biographies")

    if biographies:
        return biographies[0].get("value", "")
    else:
        return ""


def remove_birthday_and_append_note(service, contact, birthday_str):
    """
    Clears the structured birthday field on a contact,
    and appends "Birthday: <date>" to their existing notes.
    """
    resource_name = contact["resourceName"]

    # We need the contact's current etag to safely update it.
    # This prevents accidentally overwriting a change made elsewhere at the same time.
    etag = contact["etag"]

    # Build the new note text by appending to whatever was already there.
    existing_note = get_existing_note(contact)

    if existing_note.strip():
        # If there's already a note, add the birthday on a new line after it.
        new_note = f"{existing_note}\nBirthday: {birthday_str}"
    else:
        # If there's no existing note, the birthday line becomes the whole note.
        new_note = f"Birthday: {birthday_str}"

    # Send the update to Google.
    # birthdays is set to an empty list to clear it.
    # biographies is set to a single object containing our new note text.
    service.people().updateContact(
        resourceName=resource_name,
        updatePersonFields="birthdays,biographies",
        body={
            "etag": etag,
            "birthdays": [],
            "biographies": [{"value": new_note, "contentType": "TEXT_PLAIN"}],
        },
    ).execute()


def main():
    """
    Runs the full interactive review process from start to finish.
    """
    print("Logging in to Google...")
    creds = get_credentials()

    # Build the actual People API client we'll use for the rest of the script.
    service = build("people", "v1", credentials=creds)

    print("Fetching contacts with birthdays...")
    contacts = fetch_contacts_with_birthdays(service)

    print(f"Found {len(contacts)} contacts with a birthday set.\n")

    # Keep a running count so you get a summary at the end.
    kept_count = 0
    removed_count = 0

    for contact in contacts:
        name = get_display_name(contact)
        birthday_obj = contact["birthdays"][0]
        birthday_str = format_birthday(birthday_obj)

        # Skip any birthday entry that's malformed and has no usable date.
        if not birthday_str:
            continue

        # Ask the user what to do with this specific contact.
        # k = keep the birthday as-is
        # r = remove it from Calendar and move it into notes
        # q = quit the review early, leaving remaining contacts untouched
        answer = input(f"{name} — {birthday_str}   [k]eep / [r]emove / [q]uit: ").strip().lower()

        if answer == "q":
            print("Stopping early. No further contacts will be changed.")
            break

        elif answer == "r":
            remove_birthday_and_append_note(service, contact, birthday_str)
            print(f"  -> Removed birthday from Calendar, added note to {name}.\n")
            removed_count += 1

        else:
            # Any other input (including just pressing Enter) is treated as "keep".
            print(f"  -> Kept {name}'s birthday as-is.\n")
            kept_count += 1

    print("Done.")
    print(f"Kept: {kept_count}")
    print(f"Removed and moved to notes: {removed_count}")


# Only run main() if this file is executed directly, not if it's imported elsewhere.
if __name__ == "__main__":
    main()
