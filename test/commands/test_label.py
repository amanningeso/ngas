#
#    ICRAR - International Centre for Radio Astronomy Research
#    (c) UWA - The University of Western Australia, 2012
#    Copyright by UWA (in the framework of the ICRAR)
#    All rights reserved
#
#    This library is free software; you can redistribute it and/or
#    modify it under the terms of the GNU Lesser General Public
#    License as published by the Free Software Foundation; either
#    version 2.1 of the License, or (at your option) any later version.
#
#    This library is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#    Lesser General Public License for more details.
#
#    You should have received a copy of the GNU Lesser General Public
#    License along with this library; if not, write to the Free Software
#    Foundation, Inc., 59 Temple Place, Suite 330, Boston,
#    MA 02111-1307  USA
#
#******************************************************************************
#
# "@(#) $Id: ngamsLabelCmdTest.py,v 1.5 2008/08/19 20:51:50 jknudstr Exp $"
#
# Who       When        What
# --------  ----------  -------------------------------------------------------
# jknudstr  20/11/2003  Created
#
"""
This module contains the Test Suite for the LABEL Command.
"""

from ngamsLib.ngamsCore import getHostName, NGAMS_LABEL_CMD
from ..ngamsTestLib import ngamsTestSuite, sendPclCmd


class ngamsLabelCmdTest(ngamsTestSuite):
    """
    Synopsis:
    Test Suite for the LABEL Command.

    Description:
    The purpose of the Test Suite is to exercise the LABEL Command in the
    various ways this is used. This goes for:

      - Printing of labels for a disk referred to by host_id/slot_id.
      - Printing of labels for a disk referred to by its Disk ID.
      - Renaming a disk referring to the disk by its Disk ID.

    Both normal and abnormal conditions are exercised.

    Missing Test Cases:
    - Test LABEL?disk_id.
    - Test re-label feature.
    - Test illegal combinations of parameters.
    - Review Test Suite and add relevant Test Cases.
    """

    def test_LabelCmd_1(self):
        """
        Synopsis:
        Test basic handling of the LABEL Command.

        Description:
        The purpose of the Test Case is to verify the normal execution of the
        LABEL Command when specifying to print out a label for a disk referring
        to theof the disk.

        Expected Result:
        The contacted server should find the information for the

        Test Steps:
        - Start server.
        - Submit LABEL Command specifying the host_id/slot_id of the disk.
        - Verify the response from the LABEL Command.
        - Verify the printer file generated by the LABEL Printer Plug-in.

        Remarks:
        ...
        """


        # TODO: The host name is contained in the label, run only on
        #       ngasdev2 for the moment ...
        if (getHostName() != "ngasdev2"):
            return

        self.prepExtSrv()
        status = sendPclCmd().get_status(NGAMS_LABEL_CMD,
                                 pars = [["slot_id", "1"],
                                         ["host_id", getHostName()]])
        refStatFile = "ref/ngamsLabelCmdTest_test_LabelCmd_1_1_ref"
        self.assert_status_ref_file(refStatFile, status, msg="Incorrect status returned for LABEL Command")

        tmpStatFile = self.ngas_path("tmp/ngamsLabel_NGAS-" + getHostName() + "-8888.prn")
        refStatFile= "ref/ngamsLabelCmdTest_test_LabelCmd_1_2_ref.prn"
        self.checkFilesEq(refStatFile, tmpStatFile,
                          "Incorrect printer file generated by LABEL Command")