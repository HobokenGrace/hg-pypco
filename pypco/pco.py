"""The primary module for pypco containing main wrapper logic."""

import time
import logging
import re

import requests

from .auth_config import PCOAuthConfig
from .exceptions import PCORequestTimeoutException, \
    PCORequestException, PCOUnexpectedRequestException

class PCO(): #pylint: disable=too-many-instance-attributes
    """The entry point to the PCO API.

    Note:
        You must specify either an application ID and a secret or an oauth token.
        If you specify an invalid combination of these arguments, an exception will be
        raised when you attempt to make API calls.

    Args:
        application_id (str): The application_id; secret must also be specified.
        secret (str): The secret for your app; application_id must also be specified.
        token (str): OAUTH token for your app; application_id and secret must not be specified.
        api_base (str): The base URL against which REST calls will be made.
            Default: https://api.planningcenteronline.com
        timeout (int): How long to wait (seconds) for requests to timeout. Default 60.
        upload_url (str): The URL to which files will be uploaded.
            Default: https://upload.planningcenteronline.com/v2/files
        upload_timeout (int): How long to wait (seconds) for uploads to timeout. Default 300.
        timeout_retries (int): How many times to retry requests that have timed out. Default 3.
    """

    def __init__( #pylint: disable=too-many-arguments
            self,
            application_id=None,
            secret=None,
            token=None,
            api_base='https://api.planningcenteronline.com',
            timeout=60,
            upload_url='https://upload.planningcenteronline.com/v2/files',
            upload_timeout=300,
            timeout_retries=3,
        ):

        self._log = logging.getLogger(__name__)

        self._auth_config = PCOAuthConfig(application_id, secret, token)
        self._auth_header = self._auth_config.auth_header

        self.api_base = api_base
        self.timeout = timeout

        self.upload_url = upload_url
        self.upload_timeout = upload_timeout

        self.timeout_retries = timeout_retries

        self.session = requests.Session()

        self._log.debug("Pypco has been initialized!")

    def _do_request(self, method, url, payload=None, upload=None, **params):
        """Builds, executes, and performs a single request against the PCO API.

        Executed request could be one of the standard HTTP verbs or a file upload.

        Args:
            method (str): The HTTP method to use for this request.
            url (str): The URL against which this request will be executed.
            payload (obj): A json-serializable Python object to be sent as the post/put payload.
            upload(str): The path to a file to upload.
            params (obj): A dictionary or list of tuples or bytes to send in the query string.

        Returns:
            requests.Response: The response to this request.
        """

        # Standard header
        headers = {
            'User-Agent': 'pypco',
            'Authorization': self._auth_header,
        }

        # Standard params
        request_params = {
            'headers':headers,
            'params':params,
            'json':payload,
            'timeout': self.upload_timeout if upload else self.timeout
        }

        # Add files param if upload specified
        if upload:
            upload_fh = open(upload, 'rb')
            request_params['files'] = {'file': upload_fh}

        self._log.debug(
            "Executing %s request to '%s' with args %s",
            method,
            url,
            {param:value for (param, value) in request_params.items() if param != 'headers'}
        )

        # The moment we've been waiting for...execute the request
        try:
            response = self.session.request(
                method,
                url,
                **request_params
            )
        finally:
            if upload:
                upload_fh.close()

        return response

    def _do_timeout_managed_request(self, method, url, payload=None, upload=None, **params):
        """Performs a single request against the PCO API with automatic retried in case of timeout.

        Executed request could be one of the standard HTTP verbs or a file upload.

        Args:
            method (str): The HTTP method to use for this request.
            url (str): The URL against which this request will be executed.
            payload (obj): A json-serializable Python object to be sent as the post/put payload.
            upload(str): The path to a file to upload.
            params (obj): A dictionary or list of tuples or bytes to send in the query string.

        Raises:
            PCORequestTimeoutException: The request to PCO timed out the maximum number of times.

        Returns:
            requests.Response: The response to this request.
        """


        timeout_count = 0

        while True:
            try:
                return self._do_request(method, url, payload, upload, **params)

            except requests.exceptions.Timeout as exc:
                timeout_count += 1

                self._log.debug("The request to \"%s\" timed out after %d tries.", \
                    url, timeout_count)

                if timeout_count == self.timeout_retries:
                    self._log.debug("Maximum retries (%d) hit. Will raise exception.", \
                        self.timeout_retries)

                    raise PCORequestTimeoutException( \
                        "The request to \"%s\" timed out after %d tries." \
                        % (url, timeout_count)) from exc

                continue

    def _do_ratelimit_managed_request(self, method, url, payload=None, upload=None, **params):
        """Performs a single request against the PCO API with automatic rate limit handling.

        Executed request could be one of the standard HTTP verbs or a file upload.

        Args:
            method (str): The HTTP method to use for this request.
            url (str): The URL against which this request will be executed.
            payload (obj): A json-serializable Python object to be sent as the post/put payload.
            upload(str): The path to a file to upload.
            params (obj): A dictionary or list of tuples or bytes to send in the query string.

        Raises:
            PCORequestTimeoutException: The request to PCO timed out the maximum number of times.

        Returns:
            requests.Response: The response to this request.
        """


        while True:

            response = self._do_timeout_managed_request(method, url, payload, upload, **params)

            if response.status_code == 429:
                self._log.debug("Received rate limit response. Will try again after %d sec(s).", \
                    int(response.headers['Retry-After']))

                time.sleep(int(response.headers['Retry-After']))
                continue

            return response

    def _do_url_managed_request(self, method, url, payload=None, upload=None, **params):
        """Performs a single request against the PCO API, automatically cleaning up the URL.

        Executed request could be one of the standard HTTP verbs or a file upload.

        Args:
            method (str): The HTTP method to use for this request.
            url (str): The URL against which this request will be executed.
            payload (obj): A json-serializable Python object to be sent as the post/put payload.
            upload(str): The path to a file to upload.
            params (obj): A dictionary or list of tuples or bytes to send in the query string.

        Raises:
            PCORequestTimeoutException: The request to PCO timed out the maximum number of times.

        Returns:
            requests.Response: The response to this request.
        """

        self._log.debug("URL cleaning input: \"%s\"", url)

        if not upload:
            url = url if url.startswith(self.api_base) else f'{self.api_base}{url}'
            url = re.subn(r'(?<!:)[/]{2,}', '/', url)[0]

        self._log.debug("URL cleaning output: \"%s\"", url)

        return self._do_ratelimit_managed_request(method, url, payload, upload, **params)

    def request_response(self, method, url, payload=None, upload=None, **params):
        """A generic entry point for making a managed request against PCO.

        This function will return a Requests response object, allowing access to
        all request data and metadata. Executed request could be one of the standard
        HTTP verbs or a file upload. If you're just looking for your data (json), use
        the request_json() function or get(), post(), etc.

        Args:
            method (str): The HTTP method to use for this request.
            url (str): The URL against which this request will be executed.
            payload (obj): A json-serializable Python object to be sent as the post/put payload.
            upload(str): The path to a file to upload.
            params (obj): A dictionary or list of tuples or bytes to send in the query string.

        Raises:
            PCORequestTimeoutException: The request to PCO timed out the maximum number of times.
            PCOUnexpectedRequestException: An unexpected error occurred when making your request.
            PCORequestException: The response from the PCO API indicated an error with your request.

        Returns:
            requests.Response: The response to this request.
        """

        try:
            response = self._do_url_managed_request(method, url, payload, upload, **params)
        except Exception as err:
            self._log.debug("Request resulted in unexpected error: \"%s\"", str(err))
            raise PCOUnexpectedRequestException(str(err)) from err

        try:
            response.raise_for_status()
        except requests.HTTPError as err:
            self._log.debug("Request resulted in API error: \"%s\"", str(err))
            raise PCORequestException(
                response.status_code,
                str(err),
                response_body=response.text
            ) from err

        return response

    def request_json(self, method, url, payload=None, upload=None, **params):
        """A generic entry point for making a managed request against PCO.

        This function will return the payload from the PCO response (a dict).

        Args:
            method (str): The HTTP method to use for this request.
            url (str): The URL against which this request will be executed.
            payload (obj): A json-serializable Python object to be sent as the post/put payload.
            upload(str): The path to a file to upload.
            params (obj): A dictionary or list of tuples or bytes to send in the query string.

        Raises:
            PCORequestTimeoutException: The request to PCO timed out the maximum number of times.
            PCOUnexpectedRequestException: An unexpected error occurred when making your request.
            PCORequestException: The response from the PCO API indicated an error with your request.

        Returns:
            dict: The payload from the response to this request.
        """

        return self.request_response(method, url, payload, upload, **params).json()

    def get(self, url, **params):
        """Perform a GET request against the PCO API.

        Performs a fully managed GET request (handles ratelimiting, timeouts, etc.).

        Args:
            url (str): The URL against which to perform the request. Can include
                what's been set as api_base, which will be ignored if this value is also
                present in your URL.
            params: Any named arguments will be passed as query parameters. Values must
                be of type str!

        Raises:
            PCORequestTimeoutException: The request to PCO timed out the maximum number of times.
            PCOUnexpectedRequestException: An unexpected error occurred when making your request.
            PCORequestException: The response from the PCO API indicated an error with your request.

        Returns:
            dict: The payload returned by the API for this request.
        """

        return self.request_json('GET', url, **params)

    def post(self, url, payload=None, **params):
        """Perform a POST request against the PCO API.

        Performs a fully managed POST request (handles ratelimiting, timeouts, etc.).

        Args:
            url (str): The URL against which to perform the request. Can include
                what's been set as api_base, which will be ignored if this value is also
                present in your URL.
            payload (dict): The payload for the POST request. Must be serializable to JSON!
            params: Any named arguments will be passed as query parameters. Values must
                be of type str!

        Raises:
            PCORequestTimeoutException: The request to PCO timed out the maximum number of times.
            PCOUnexpectedRequestException: An unexpected error occurred when making your request.
            PCORequestException: The response from the PCO API indicated an error with your request.

        Returns:
            dict: The payload returned by the API for this request.
        """

        return self.request_json('POST', url, payload, **params)

    def patch(self, url, payload=None, **params):
        """Perform a PATCH request against the PCO API.

        Performs a fully managed PATCH request (handles ratelimiting, timeouts, etc.).

        Args:
            url (str): The URL against which to perform the request. Can include
                what's been set as api_base, which will be ignored if this value is also
                present in your URL.
            payload (dict): The payload for the PUT request. Must be serializable to JSON!
            params: Any named arguments will be passed as query parameters. Values must
                be of type str!

        Raises:
            PCORequestTimeoutException: The request to PCO timed out the maximum number of times.
            PCOUnexpectedRequestException: An unexpected error occurred when making your request.
            PCORequestException: The response from the PCO API indicated an error with your request.

        Returns:
            dict: The payload returned by the API for this request.
        """

        return self.request_json('PATCH', url, payload, **params)

    def delete(self, url, **params):
        """Perform a DELETE request against the PCO API.

        Performs a fully managed DELETE request (handles ratelimiting, timeouts, etc.).

        Args:
            url (str): The URL against which to perform the request. Can include
                what's been set as api_base, which will be ignored if this value is also
                present in your URL.
            params: Any named arguments will be passed as query parameters. Values must
                be of type str!

        Raises:
            PCORequestTimeoutException: The request to PCO timed out the maximum number of times.
            PCOUnexpectedRequestException: An unexpected error occurred when making your request.
            PCORequestException: The response from the PCO API indicated an error with your request.

        Returns:
            requests.Response: The response object returned by the API for this request.
            A successful delete request will return a response with an empty payload,
            so we return the response object here instead.
        """

        return self.request_response('DELETE', url, **params)

    def iterate(self, url, offset=0, per_page=25, **params): #pylint: disable=too-many-branches
        """Iterate a list of objects in a response, handling pagination.

        Basically, this function wraps get in a generator function designed for
        processing requests that will return multiple objects. Pagination is
        transparently handled.

        Objects specified as includes will be injected into their associated
        object and returned.

        Args:
            url (str): The URL against which to perform the request. Can include
                what's been set as api_base, which will be ignored if this value is also
                present in your URL.
            offset (int): The offset at which to start. Usually going to be 0 (the default).
            per_page (int): The number of results that should be requested in a single page.
                Valid values are 1 - 100, defaults to the PCO default of 25.
            params: Any additional named arguments will be passed as query parameters. Values must
                be of type str!

        Raises:
            PCORequestTimeoutException: The request to PCO timed out the maximum number of times.
            PCOUnexpectedRequestException: An unexpected error occurred when making your request.
            PCORequestException: The response from the PCO API indicated an error with your request.

        Yields:
            dict: Each object returned by the API for this request. Returns "data",
            "included", and "meta" nodes for each response. Note that data is processed somewhat
            before being returned from the API. Namely, includes are injected into the object(s)
            with which they are associated. This makes it easier to process includes associated with
            specific objects since they are accessible directly from each returned object.
        """

        while True: #pylint: disable=too-many-nested-blocks

            response = self.get(url, offset=offset, per_page=per_page, **params)

            for cur in response['data']:
                record = {
                    'data': cur,
                    'included': [],
                    'meta': {}
                }

                if 'can_include' in response['meta']:
                    record['meta']['can_include']: response['meta']['can_include']

                if 'parent' in response['meta']:
                    record['meta']['parent']: response['meta']['parent']

                if 'relationships' in cur:
                    for key in cur['relationships']:
                        relationships = cur['relationships'][key]['data']

                        if relationships is not None:
                            if isinstance(relationships, dict):
                                for include in response['included']:
                                    if include['type'] == relationships['type'] and \
                                        include['id'] == relationships['id']:

                                        record['included'].append(include)

                            elif isinstance(relationships, list):
                                for relationship in relationships:
                                    for include in response['included']:
                                        if include['type'] == relationship['type'] and \
                                            include['id'] == relationship['id']:

                                            record['included'].append(include)

                yield record

            offset += per_page

            if not 'next' in response['links']:
                break

    def upload(self, file_path, **params):
        """Upload the file at the specified path to PCO.

        Args:
            file_path (str): The path to the file to be uploaded to PCO.
            params: Any named arguments will be passed as query parameters. Values must
                be of type str!

        Raises:
            PCORequestTimeoutException: The request to PCO timed out the maximum number of times.
            PCOUnexpectedRequestException: An unexpected error occurred when making your request.
            PCORequestException: The response from the PCO API indicated an error with your request.

        Returns:
            dict: The PCO response from the file upload.
        """

        return self.request_json('POST', self.upload_url, upload=file_path, **params)

    def __del__(self):
        """Close the requests session when the PCO object goes out of scope."""

        self.session.close()

    @staticmethod
    def template(object_type, attributes=None):
        """Get template JSON for creating a new object.

        Args:
            object_type (str): The type of object to be created.
            attributes (dict): The new objects attributes. Defaults to empty.

        Returns:
            dict: A template from which to set the new object's attributes.
        """

        return {
            'data': {
                'type': object_type,
                'attributes': {} if attributes is None else attributes
            }
        }

    def refresh_list(self, pco_list_id):
        """ Updates the given Planning Center List.

            Args:
                pco_list_id (int): The ID of the given Planning Center List.

            Returns:
                dict: The payload returned by the API for this request.
        """
    # PCO API endpoint for running a given List
    return self.post(f"/people/v2/lists/{pco_list_id}/run")


    def get_list_attr(self, pco_list_id):
        """ Provides the attributes dictionary for the given Planning Center List.

            Args:
                pco_list_id (int): The ID of the given Planning Center List.

            Returns:
                dict: {

                    "auto_refresh": boolean
                    "automations_active": boolean
                    "automations_count": int
                    "batch_completed_at": datetime-tz str
                    "created_at": datetime-tz str
                    "description": str
                    "has_inactive_results": boolean
                    "include_inactive": boolean
                    "invalid": boolean
                    "name": str
                    "recently_viewed": boolean
                    "refreshed_at": datetime-tz str
                    "return_original_if_none": boolean
                    "returns": str
                    "starred": boolean
                    "status": str
                    "subset": str
                    "total_people": int
                    "updated_at": datetime-tz str

                }


        """
        # Endpoint for request for desired PCO List
        e = f"/people/v2/lists/{pco_list_id}"
        # Response in JSON format
        d = self.get(e)
        # Ensure it's a List
        if d['data']['type'] == 'List':
            # Return List dictionary
            return d['data']['attributes']


    def get_list_members(self, pco_list_id):
        """ Provides the members of the given Planning Center List.

            Args:
                pco_list_id (int): The ID of the given Planning Center List.

            Returns:
                dict: {

                    "PersonID": int ,
                    "PersonName": str ,
                    "EmailAddress": str ,
                    "PhoneNumber": str

                }
        """
        # Endpoint for request for desired PCO List
        e = f"/people/v2/lists/{pco_list_id}/people"
        # List of People data
        d = self.iterate(e, per_page=100)
        l = []
        # Ensure List has People
        if d:
            # Iterate over List People
            for i in d:
                # Ensure Person
                if i['data']['type'] == 'Person':
                    # Append Person
                    l.append(
                        {
                            'PersonID': i['data']['id'],
                            'PersonName': i['data']['attributes']['name'],
                            'EmailAddress': get_person_email(i['data']['id']),
                            'PhoneNumber': get_person_phone_number(i['data']['id'])
                        }
                    )
        # Return list of People dicts
        return l


    def get_person_email(self, pco_person_id):
        """ Provides the primary email address of the given Planning Center Person.

            Args:
                pco_person_id (int): The ID of the given Planning Center Person.

            Returns:
                str: person@domain.com

        """
        # Endpoint for request
        e = f"/people/v2/people/{pco_person_id}/emails"
        # List of Email data
        d = self.get(e)
        # Ensure Person has Email(s)
        if d:
            # Iterate over Email(s)
            for i in d['data']:
                # Ensure primary Email
                if i['type'] == 'Email' and i['attributes']['primary']:
                    # Return Email
                    return (i['attributes']['address'].lower())


    def get_person_phone_number(self, pco_person_id):
        """ Provides the primary phone number of the given Planning Center Person.

            Args:
                pco_person_id (int): The ID of the given Planning Center Person.

            Returns:
                str: 1234567890

        """
        # Endpoint for request
        e = f"/people/v2/people/{pco_person_id}/phone_numbers"
        # List of Phone Number data
        d = self.get(e)
        # Ensure Person has Phone Number(s)
        if d:
            # Iterate over Phone Number(s)
            for i in d['data']:
                # Ensure primary Phone Number
                if i['type'] == 'PhoneNumber' and i['attributes']['primary']:
                    # Return Phone Number
                    return ''.join(re.findall(r'\d+', i['attributes']['number']))


    def get_teams(self):
        """ Provides a list of all Planning Center Service Teams and their members.

            Returns:
                dict: {

                    "TeamName": str ,
                    "PersonID": int

                }

        """
        # Endpoint for request
        e = "/services/v2/teams"
        # List of Team data
        d = self.iterate(e, per_page=100)
        l = []
        # Iterate over Teams
        for i in d:
            # Ensure Team
            if i['data']['type'] == 'Team':
                # List of People in Team
                p = get_pco_team_members(i['data']['id'])
                # Iterate over People
                for j in p:
                    # Append Team
                    l.append(
                        {
                            'TeamName': i['data']['attributes']['name'],
                            'PersonID': j
                        }
                    )
        # Return list of Team dicts
        return l


    def get_team_members(self, pco_team_id):
        """ Provides a list of Person ID's for all People in given Planning Center Service Team.

            Args:
                pco_team_id (int): The ID of the given Planning Center Team.

            Returns:
                list: ID's of all Planning Center Persons in the Team.

        """
        # Endpoint for request
        e = f"/services/v2/teams/{pco_team_id}/people"
        # List of Team People data
        d = self.iterate(e, per_page=100)
        l = []
        # Ensure Team has People
        if d:
            # Iterate over Team People
            for i in d:
                # Ensure Person
                if i['data']['type'] == 'Person':
                    # Append Person
                    l.append(i['data']['id'])
            # Return list of People
        return l


    def get_groups(self):
        """ Provides a list of all Planning Center Groups and their members.

            Returns:
                dict: {

                    "GroupName": str ,
                    "PersonID": int

                }


        """
        # Endpoint for request
        e = "/groups/v2/groups"
        # List of Group data
        d = self.iterate(e, per_page=100)
        l = []
        # Iterate over Groups
        for i in d:
            # Ensure Group
            if i['data']['type'] == 'Group':
                 # List of People in Group
                p = get_pco_group_members(i['data']['id'])
                # Iterate over People
                for j in p:
                    # Append Group
                    l.append(
                        {
                            'GroupName': i['data']['attributes']['name'],
                            'PersonID': j
                        }
                    )
        # Return list of Group dicts
        return l


    def get_group_members(self, pco_group_id):
        """ Provides a list of Person ID's for all People in given Planning Center Group.

            Args:
                pco_group_id (int): The ID of the given Planning Center Group.

            Returns:
                list: ID's of all Planning Center Persons in the Group.

        """
        # Endpoint for request
        e = f"/groups/v2/groups/{pco_group_id}/people"
        # List of Team People data
        d = self.iterate(e, per_page=100)
        l = []
        # Ensure Group has People
        if d:
            # Iterate over People
            for i in d:
                # Ensure Person
                if i['data']['type'] == 'Person':
                    # Append Person
                    l.append(i['data']['id'])
        # Return list of People
        return l
