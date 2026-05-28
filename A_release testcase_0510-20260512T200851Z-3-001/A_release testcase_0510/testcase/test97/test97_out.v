module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n7,
    n8,
    n6,
    n12,
    n11,
    n9,
    n10
);

  input n0;
  input n1;
  input [3:0] n2;
  input [3:0] n3;
  input n4;
  input n5;
  output n7;
  output n8;
  output n6;
  output n12;
  output n11;
  output n9;
  output n10;

  wire \$and$testcase/test97/test97.v:20$4_Y , \$and$testcase/test97/test97.v:31$17_Y , \$and$testcase/test97/test97.v:35$22_Y , \$auto$rtlil.cc:3255:Not$40 , ___xor_testcase_test97_test97_v_23_10_n1_0__, ___xor_testcase_test97_test97_v_23_10_n2_0__, ___xor_testcase_test97_test97_v_23_10_n3_0__, ___xor_testcase_test97_test97_v_26_12_n1_0__, ___xor_testcase_test97_test97_v_26_12_n2_0__, ___xor_testcase_test97_test97_v_26_12_n3_0__, ___xor_testcase_test97_test97_v_30_16_n1_0__, ___xor_testcase_test97_test97_v_30_16_n2_0__, ___xor_testcase_test97_test97_v_30_16_n3_0__, ___xor_testcase_test97_test97_v_34_21_n1_0__, ___xor_testcase_test97_test97_v_34_21_n2_0__, ___xor_testcase_test97_test97_v_34_21_n3_0__, ___xor_testcase_test97_test97_v_49_31_n1_0__, ___xor_testcase_test97_test97_v_49_31_n2_0__, ___xor_testcase_test97_test97_v_49_31_n3_0__, n13, n14, n15, n16, n17, n18, n19, n30, n31, n40, n41, n42, n43, n44, n45, n46, n47, n56, n57, n63, n65, n80, n81, n82;

  and   \$and$testcase/test97/test97.v:50$32  (n80, n2[0], n2[1]);
  and   \$and$testcase/test97/test97.v:16$1  (n13, n2[0], n3[0]);
  and   \$and$testcase/test97/test97.v:25$11  (n30, n2[1], n3[1]);
  and   \$and$testcase/test97/test97.v:28$14  (n40, n2[2], n3[2]);
  and   \$and$testcase/test97/test97.v:32$19  (n44, n2[2], n3[2]);
  nand  xor_nand_6 (___xor_testcase_test97_test97_v_30_16_n1_0__, n2[2], n3[2]);
  nand  xor_nand_9 (___xor_testcase_test97_test97_v_34_21_n1_0__, n2[2], n3[2]);
  not   \$not$testcase/test97/test97.v:44$36  (n63, n4);
  and   \$and$testcase/test97/test97.v:31$17  (\$and$testcase/test97/test97.v:31$17_Y , n2[2], n5);
  and   \$and$testcase/test97/test97.v:35$22  (\$and$testcase/test97/test97.v:35$22_Y , n2[2], n5);
  or    \$or$testcase/test97/test97.v:29$15  (n41, n2[2], n5);
  or    \$or$testcase/test97/test97.v:33$20  (n45, n2[2], n5);
  or    \$or$testcase/test97/test97.v:51$33  (n81, n80, n3[0]);
  or    \$or$testcase/test97/test97.v:17$2  (n14, n13, n4);
  nand  xor_nand_3 (___xor_testcase_test97_test97_v_26_12_n1_0__, n30, n5);
  nand  xor_nand_7 (___xor_testcase_test97_test97_v_30_16_n2_0__, n2[2], ___xor_testcase_test97_test97_v_30_16_n1_0__);
  nand  xor_nand_8 (___xor_testcase_test97_test97_v_30_16_n3_0__, n3[2], ___xor_testcase_test97_test97_v_30_16_n1_0__);
  nand  xor_nand_10 (___xor_testcase_test97_test97_v_34_21_n2_0__, n2[2], ___xor_testcase_test97_test97_v_34_21_n1_0__);
  nand  xor_nand_11 (___xor_testcase_test97_test97_v_34_21_n3_0__, n3[2], ___xor_testcase_test97_test97_v_34_21_n1_0__);
  not   \$not$testcase/test97/test97.v:31$18  (n43, \$and$testcase/test97/test97.v:31$17_Y );
  not   \$not$testcase/test97/test97.v:35$23  (n47, \$and$testcase/test97/test97.v:35$22_Y );
  or    \$or$testcase/test97/test97.v:36$24  (n56, n40, n41);
  not   \$not$testcase/test97/test97.v:52$38  (n82, n81);
  not   \$not$testcase/test97/test97.v:18$34  (n15, n14);
  nand  xor_nand_4 (___xor_testcase_test97_test97_v_26_12_n2_0__, n30, ___xor_testcase_test97_test97_v_26_12_n1_0__);
  nand  xor_nand_5 (___xor_testcase_test97_test97_v_26_12_n3_0__, n5, ___xor_testcase_test97_test97_v_26_12_n1_0__);
  nand  \$xor$testcase/test97/test97.v:30$16  (n42, ___xor_testcase_test97_test97_v_30_16_n2_0__, ___xor_testcase_test97_test97_v_30_16_n3_0__);
  nand  \$xor$testcase/test97/test97.v:34$21  (n46, ___xor_testcase_test97_test97_v_34_21_n2_0__, ___xor_testcase_test97_test97_v_34_21_n3_0__);
  or    \$or$testcase/test97/test97.v:19$3  (n16, n15, n5);
  nand  \$xor$testcase/test97/test97.v:26$12  (n31, ___xor_testcase_test97_test97_v_26_12_n2_0__, ___xor_testcase_test97_test97_v_26_12_n3_0__);
  or    \$or$testcase/test97/test97.v:37$25  (n57, n56, n42);
  and   \$and$testcase/test97/test97.v:20$4  (\$and$testcase/test97/test97.v:20$4_Y , n16, n2[1]);
  or    \$or$testcase/test97/test97.v:27$13  (n7, n31, n4);
  or    \$or$testcase/test97/test97.v:38$26  (n8, n57, n43);
  not   \$not$testcase/test97/test97.v:20$5  (n17, \$and$testcase/test97/test97.v:20$4_Y );
  and   \$and$testcase/test97/test97.v:47$30  (n65, n31, n8);
  or    \$or$testcase/test97/test97.v:21$6  (\$auto$rtlil.cc:3255:Not$40 , n17, n3[1]);
  dff g32 (.Q(n11), .RN(n1), .SN(1'b1), .CK(n0), .D(n65));
  not   \$not$testcase/test97/test97.v:21$7  (n18, \$auto$rtlil.cc:3255:Not$40 );
  not   \$not$testcase/test97/test97.v:22$9  (n19, \$auto$rtlil.cc:3255:Not$40 );
  nand  xor_nand_0 (___xor_testcase_test97_test97_v_23_10_n1_0__, n19, n2[2]);
  nand  xor_nand_1 (___xor_testcase_test97_test97_v_23_10_n2_0__, n19, ___xor_testcase_test97_test97_v_23_10_n1_0__);
  nand  xor_nand_2 (___xor_testcase_test97_test97_v_23_10_n3_0__, n2[2], ___xor_testcase_test97_test97_v_23_10_n1_0__);
  nand  \$xor$testcase/test97/test97.v:23$10  (n6, ___xor_testcase_test97_test97_v_23_10_n2_0__, ___xor_testcase_test97_test97_v_23_10_n3_0__);
  nand  xor_nand_12 (___xor_testcase_test97_test97_v_49_31_n1_0__, n6, n7);
  nand  xor_nand_13 (___xor_testcase_test97_test97_v_49_31_n2_0__, n6, ___xor_testcase_test97_test97_v_49_31_n1_0__);
  nand  xor_nand_14 (___xor_testcase_test97_test97_v_49_31_n3_0__, n7, ___xor_testcase_test97_test97_v_49_31_n1_0__);
  nand  \$xor$testcase/test97/test97.v:49$31  (n12, ___xor_testcase_test97_test97_v_49_31_n2_0__, ___xor_testcase_test97_test97_v_49_31_n3_0__);

  assign n9 = 1'b1;
  assign n10 = n4;

endmodule

(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);
endmodule
